"""Modelo de evaluación multinivel. Funciones puras: sin red ni E/S.

Por cada hora, spot y nivel se calculan dos puntuaciones:
  - display: decide el color y la tabla. Lleva las dos pinzas del modelo acumulativo.
  - notify:  decide si el aviso suena. Sin pinzas, y a 0 cuando la ola es más pequeña
             que el tamaño ideal del nivel (ese 🟡 es de cortesía, no un día bueno).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .config import Config, Level, Spot
from .fetch import Hour, SpotForecast

GREEN, YELLOW, RED, FLAT, DANGER = "green", "yellow", "red", "flat", "danger"
RANK = {GREEN: 4, YELLOW: 3, RED: 2, FLAT: 1, DANGER: 0}
EMOJI = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴", FLAT: "⚪", DANGER: "⛔"}
LABEL = {GREEN: "MERECE LA PENA", YELLOW: "JUSTITO", RED: "NO MERECE LA PENA",
         FLAT: "FLAT", DANGER: "PELIGROSO"}

OFFSHORE, CROSS, ONSHORE, CALM = "terral", "cruzado", "de mar", "calma"


# --- Utilidades ---------------------------------------------------------------

def interp(x: float, points) -> float:
    """Interpolación lineal; fuera del rango se mantiene el valor del extremo."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def angle_diff(a: float, b: float) -> float:
    """Diferencia angular en 0–180°."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def circular_mean(angles) -> float:
    angles = list(angles)
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c)) % 360


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs)


def fmt_num(x: float) -> str:
    """0.8 → '0,8'; 7.0 → '7'."""
    s = f"{x:.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


# --- Física por hora ------------------------------------------------------------

def dir_factor(wave_dir: float, facing: float) -> float:
    d = angle_diff(wave_dir, facing)
    return interp(d, ((30, 1.0), (60, 0.7), (90, 0.3), (110, 0.0)))


def heff(wave_height: float, wave_dir: float, spot: Spot) -> float:
    return wave_height * dir_factor(wave_dir, spot.facing) * spot.exposure


def wind_kind(wind_dir: float, spot: Spot, rules: dict, speed: float | None = None) -> str:
    w = rules["wind"]
    if speed is not None and speed <= w["calm_max"]:
        return CALM
    rel = angle_diff(wind_dir, (spot.facing + 180) % 360)
    if rel <= w["offshore_max_rel"]:
        return OFFSHORE
    if rel <= w["cross_max_rel"]:
        return CROSS
    return ONSHORE


def wind_adjust(kind: str, speed: float, rules: dict) -> float:
    if kind == CALM:
        return float(rules["wind"]["calm_adjust"])
    table = rules["wind"][{OFFSHORE: "offshore", CROSS: "cross", ONSHORE: "onshore"}[kind]]
    for limit, adj in table:
        if speed <= limit:
            return float(adj)
    return float(table[-1][1])


def danger_ceiling(level: Level, period: float, rules: dict) -> float:
    """Techo de Heff, continuo con el periodo (a más periodo, más energía por metro)."""
    f = 1 - rules["danger_period_slope"] * (period - rules["danger_period_ref"])
    return level.heff_danger * min(1.0, max(rules["danger_period_min_factor"], f))


@dataclass
class Conditions:
    """Lo que hace falta para puntuar una hora en un spot."""
    wave_height: float
    wave_direction: float
    period: float                 # pico
    wind_speed: float
    wind_direction: float
    gusts: float
    swell_height: float = 0.0
    t_estimated: bool = False
    swell_period: float = 0.0

    @classmethod
    def from_hour(cls, h: Hour) -> "Conditions":
        return cls(h.wave_height, h.wave_direction, h.peak_period, h.wind_speed,
                   h.wind_direction, h.gusts, h.swell_height or 0.0, h.t_estimated,
                   h.swell_period or 0.0)


@dataclass
class HourScore:
    level: str
    verdict: str
    display: float
    notify: float
    heff: float
    period: float
    wind_kind: str
    reason: str = ""
    vetoed: bool = False
    doubtful: bool = False
    t_estimated: bool = False


def verdict_for(score: float, rules: dict) -> str:
    if score >= rules["verdict_green"]:
        return GREEN
    if score >= rules["verdict_yellow"]:
        return YELLOW
    return RED


def is_doubtful(score: float, rules: dict) -> bool:
    m = rules["doubt_margin"]
    return any(abs(score - t) < m for t in (rules["verdict_green"], rules["verdict_yellow"]))


def _score_level(c: Conditions, spot: Spot, level: Level, rules: dict) -> HourScore:
    """Vetos y puntuación de un nivel, sin la pinza B (que depende de los otros niveles)."""
    h = heff(c.wave_height, c.wave_direction, spot)
    kind = wind_kind(c.wind_direction, spot, rules, c.wind_speed)
    common = dict(level=level.id, heff=h, period=c.period, wind_kind=kind,
                  t_estimated=c.t_estimated)

    def veto(verdict, reason):
        return HourScore(verdict=verdict, display=0.0, notify=0.0, reason=reason,
                         vetoed=True, **common)

    # 1. Peligro
    if h > danger_ceiling(level, c.period, rules):
        return veto(DANGER, f"demasiado grande para tu nivel ({fmt_num(h)} m)")
    if c.wind_speed >= level.wind_max:
        return veto(DANGER, f"viento muy fuerte ({round(c.wind_speed)} km/h)")
    if c.gusts >= level.gust_max:
        return veto(DANGER, f"rachas de {round(c.gusts)} km/h")
    # 2. Flat
    if h < level.flat_threshold:
        return veto(FLAT, f"flat ({fmt_num(h)} m)")
    # 3. Periodo. Con pico estimado se relaja: el factor medio→pico tiene ±14 % de error.
    t_min = level.t_peak_min - (rules["t_estimated_margin"] if c.t_estimated else 0)
    if c.period < t_min:
        return veto(RED, f"periodo muy corto ({fmt_num(c.period)} s): mar de viento")
    # 4. Viento de mar fuerte
    if kind == ONSHORE and c.wind_speed > level.onshore_veto:
        return veto(RED, f"viento de mar {round(c.wind_speed)} km/h: revuelto")

    size = interp(h, level.size_curve)
    per = interp(c.period, rules["period_curve"])
    score = min(0.5 * size + 0.5 * per, min(size, per) + rules["base_cap_margin"])
    adj = wind_adjust(kind, c.wind_speed, rules)
    score += adj
    if (c.wave_height > 0 and c.swell_height >= rules["swell_ratio"] * c.wave_height
            and c.swell_period >= rules["swell_min_period"]):
        score += rules["swell_bonus"]
    raw = max(0.0, min(10.0, score))

    small = h < level.heff_ideal_min
    display = min(raw, rules["small_day_cap"]) if small else raw   # pinza A
    notify = 0.0 if small else raw
    if c.period < rules["green_min_period"]:
        # Periodo corto: como mucho 🟡, ni en el color ni en el aviso.
        display = min(display, rules["short_period_cap"])
        notify = min(notify, rules["short_period_cap"])

    # Motivo: lo que más ha limitado la puntuación.
    if adj < 0:
        # Cualquier viento que reste es lo que hay que contar: con olas pequeñas decide el color.
        what = {OFFSHORE: "terral fuerte", CROSS: "viento cruzado", ONSHORE: "viento de mar"}[kind]
        reason = f"{what} {round(c.wind_speed)} km/h"
    elif small:
        reason = f"pequeño para tu nivel ({fmt_num(h)} m)"
    elif per < size:
        reason = f"periodo corto ({fmt_num(c.period)} s)"
    elif h > max(x for x, y in level.size_curve if y == max(v for _, v in level.size_curve)):
        reason = f"grande para tu nivel ({fmt_num(h)} m)"
    else:
        reason = f"pequeño ({fmt_num(h)} m)"
    return HourScore(verdict=RED, display=display, notify=notify, reason=reason, **common)


def score_hour(c: Conditions, spot: Spot, config: Config) -> list[HourScore]:
    """Puntúa una hora para todos los niveles, de menor a mayor, aplicando la pinza B."""
    rules = config.rules
    out: list[HourScore] = []
    for level in config.levels:
        s = _score_level(c, spot, config.level_at(level, spot), rules)
        if not s.vetoed:
            # Pinza B: si un nivel inferior tiene día, este no puede quedar en rojo.
            if any(o.display >= rules["verdict_yellow"] for o in out):
                s.display = max(s.display, rules["cumulative_floor"])
            s.verdict = verdict_for(s.display, rules)
            s.doubtful = is_doubtful(s.display, rules)
            if s.verdict == GREEN:
                s.reason = ""
        out.append(s)
    return out


# --- Tabla sugerida -----------------------------------------------------------

def board_for(level_id: str, h: float, period: float, config: Config) -> str:
    p = round(h * period, 2)
    for limit, name in config.boards[level_id]["cuts"]:
        if p < limit:
            return name
    return config.boards[level_id]["cuts"][-1][1]


# --- Veredicto del día ----------------------------------------------------------

@dataclass
class DayVerdict:
    spot_id: str
    level: str
    day: date
    verdict: str
    score: float                  # media de la ventana, o la mejor hora si no hay ventana
    window: tuple[int, int] | None = None   # (hora inicio, hora fin exclusiva)
    single_hour: bool = False
    doubtful: bool = False
    reason: str = ""
    notify: bool = False
    board: str = ""
    start: datetime | None = None
    heff: float = 0.0
    wave_height: float = 0.0
    period: float = 0.0
    t_estimated: bool = False
    wave_direction: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    wind_kind: str = ""
    timeline: list[tuple[int, str, str]] = field(default_factory=list)  # (hora, veredicto, motivo)
    star_window: tuple[int, int] | None = None   # mejor franja real (≥ window_min_hours horas)
    star_score: float = 0.0
    stars: int = 0

    @property
    def good(self) -> bool:
        return self.verdict in (GREEN, YELLOW)


def daylight_hours(sf: SpotForecast, day: date) -> list[Hour]:
    """Horas completas entre media hora antes del amanecer y media hora antes del atardecer."""
    if day not in sf.sun:
        return []
    rise, sunset = sf.sun[day]
    lo, hi = rise - timedelta(minutes=30), sunset - timedelta(minutes=30)
    return [h for h in sf.hours if h.time.date() == day and lo <= h.time <= hi and h.complete()]


def _runs(times: list[datetime], ok: list[bool]) -> list[tuple[int, int]]:
    """Rachas (inicio, fin inclusivo) de índices con ok=True y horas consecutivas."""
    runs, start = [], None
    for i, flag in enumerate(ok):
        if flag and start is not None and times[i] - times[i - 1] != timedelta(hours=1):
            runs.append((start, i - 1))
            start = None
        if flag and start is None:
            start = i
        if not flag and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(ok) - 1))
    return runs


def day_verdict(series: list[tuple[Hour, HourScore]], spot_id: str, level_id: str,
                day: date, config: Config) -> DayVerdict:
    rules = config.rules
    if not series:
        return DayVerdict(spot_id, level_id, day, RED, 0.0, reason="sin datos")
    times = [h.time for h, _ in series]
    scores = [s for _, s in series]
    y = rules["verdict_yellow"]
    min_h = rules["window_min_hours"]

    runs = _runs(times, [s.display >= y for s in scores])
    if runs:
        a, b = max(runs, key=lambda r: (r[1] - r[0], mean(s.display for s in scores[r[0]:r[1] + 1])))
        chosen = list(range(a, b + 1))
        avg = mean(scores[i].display for i in chosen)
        verdict = verdict_for(avg, rules)
        single = len(chosen) < min_h
        if single and verdict == GREEN:
            verdict = YELLOW            # una sola hora buena no es fiable
        mid = scores[chosen[len(chosen) // 2]]
        dv = DayVerdict(spot_id, level_id, day, verdict, avg,
                        window=(times[a].hour, times[b].hour + 1), single_hour=single,
                        doubtful=is_doubtful(avg, rules),
                        reason="" if verdict == GREEN else mid.reason)
    else:
        best = max(range(len(scores)), key=lambda i: (RANK[scores[i].verdict], scores[i].display, -i))
        chosen = [best]
        s = scores[best]
        dv = DayVerdict(spot_id, level_id, day, s.verdict, s.display, reason=s.reason)

    # Nota real (sin las pinzas de cortesía del modelo acumulativo): decide el sonido del
    # aviso y las estrellas. Solo cuentan franjas de al menos min_h horas seguidas.
    nruns = [(a, b) for a, b in _runs(times, [s.notify >= y for s in scores]) if b - a + 1 >= min_h]
    dv.notify = any(mean(s.notify for s in scores[a:b + 1]) >= rules["notify_min"] for a, b in nruns)
    if nruns:
        a, b = max(nruns, key=lambda r: (mean(s.notify for s in scores[r[0]:r[1] + 1]), r[1] - r[0]))
        dv.star_window = (times[a].hour, times[b].hour + 1)
        dv.star_score = mean(s.notify for s in scores[a:b + 1])
        chosen = list(range(a, b + 1))      # las condiciones que se cuentan son las de esa franja

    hours = [series[i][0] for i in chosen]
    dv.start = hours[0].time
    dv.heff = mean(scores[i].heff for i in chosen)
    dv.wave_height = mean(h.wave_height for h in hours)
    dv.period = mean(h.peak_period for h in hours)
    dv.t_estimated = any(h.t_estimated for h in hours)
    dv.wave_direction = circular_mean(h.wave_direction for h in hours)
    dv.wind_speed = mean(h.wind_speed for h in hours)
    dv.wind_direction = circular_mean(h.wind_direction for h in hours)
    dv.wind_kind = (CALM if round(dv.wind_speed) <= rules["wind"]["calm_max"]
                    else scores[chosen[len(chosen) // 2]].wind_kind)
    dv.board = board_for(level_id, dv.heff, dv.period, config)
    # La tira horaria enseña la nota real de cada hora, no la de cortesía.
    dv.timeline = [(h.time.hour, s.verdict if s.vetoed else verdict_for(s.notify, rules), s.reason)
                   for h, s in series]
    dv.stars = stars_for(dv.star_score, config) if dv.star_window else 0
    return dv


def stars_for(score: float, config: Config) -> int:
    """0–5 estrellas según la nota media real de la mejor franja."""
    stars = 0
    for cut, n in config.stars["cuts"]:
        if score >= cut:
            stars = n
    return stars


def evaluate_spot_day(sf: SpotForecast, spot: Spot, config: Config,
                      day: date) -> dict[str, DayVerdict]:
    hours = daylight_hours(sf, day)
    scored = [(h, score_hour(Conditions.from_hour(h), spot, config)) for h in hours]
    return {
        level.id: day_verdict([(h, s[i]) for h, s in scored], spot.id, level.id, day, config)
        for i, level in enumerate(config.levels)
    }


# --- Resumen del día -------------------------------------------------------------

@dataclass
class BeachDay:
    """Una playa en un día: sus estrellas son las del nivel que mejor lo tiene."""
    spot_id: str
    stars: int
    best: DayVerdict               # el nivel más bajo con esas estrellas
    levels: list[DayVerdict]       # los niveles aptos en esa playa, de menor a mayor
    top_levels: list[str]          # los niveles que alcanzan esas estrellas


@dataclass
class DaySummary:
    day: date
    verdicts: dict[str, dict[str, DayVerdict]]   # spot → nivel → veredicto
    best: dict[str, DayVerdict]                  # nivel → mejor spot apto para ese nivel
    headline: DayVerdict
    notify: bool
    cleanup: bool = False
    rain_mm: float = 0.0
    sst: float | None = None
    beaches: list[BeachDay] = field(default_factory=list)   # en el orden de config (N → S)

    @property
    def good(self) -> bool:
        return self.headline.good

    @property
    def starred(self) -> list[BeachDay]:
        """Las playas con al menos una estrella, de mejor a peor."""
        return sorted((b for b in self.beaches if b.stars > 0),
                      key=lambda b: (b.stars, rank_key(b.best)), reverse=True)


def beach_day(spot: Spot, verdicts: dict[str, DayVerdict], config: Config) -> BeachDay:
    levels = [verdicts[lv.id] for lv in config.levels if spot.allows(lv.id)]
    top = max(dv.stars for dv in levels)
    best = next(dv for dv in levels if dv.stars == top)
    return BeachDay(spot.id, top, best, levels, [dv.level for dv in levels if dv.stars == top])


def rank_key(dv: DayVerdict):
    """Mejor color primero; a igual color, una franja de varias horas antes que una hora
    suelta; luego la puntuación y la duración."""
    w = (dv.window[1] - dv.window[0]) if dv.window else 0
    return (RANK[dv.verdict], not dv.single_hour, dv.score, w)


def best_for_level(verdicts: dict[str, dict[str, DayVerdict]], config: Config,
                   level_id: str) -> DayVerdict | None:
    cands = [verdicts[s.id][level_id] for s in config.spots
             if s.allows(level_id) and s.id in verdicts]
    return max(cands, key=rank_key) if cands else None


def is_cleanup_day(sf: SpotForecast, spot: Spot, dv: DayVerdict, config: Config) -> bool:
    """🧹 Ayer mar de viento con oleaje ≥ el de ahora; hoy terral o calma."""
    if dv.start is None:
        return False
    a = config.alerts
    prev = [h for h in sf.hours
            if dv.start - timedelta(hours=24) <= h.time < dv.start
            and h.wave_height is not None and h.wind_speed is not None]
    if len(prev) < 12:
        return False
    if mean(h.wave_height for h in prev) < dv.wave_height:
        return False
    prev_dir = circular_mean(h.wind_direction for h in prev)
    if wind_kind(prev_dir, spot, config.rules) != ONSHORE:
        return False
    if mean(h.wind_speed for h in prev) <= a["cleanup_onshore_mean"]:
        return False
    return dv.wind_kind == OFFSHORE or dv.wind_speed <= a["cleanup_calm_max"]


def rain_48h(data: dict[str, SpotForecast], config: Config, ref: datetime) -> float:
    """Lluvia acumulada en las 48 h previas a ref; el máximo entre los puntos de Barcelona."""
    totals = []
    for sid in config.alerts["rain_spots"]:
        sf = data.get(sid)
        if sf:
            totals.append(sum(h.precipitation or 0.0 for h in sf.hours
                              if ref - timedelta(hours=48) <= h.time < ref))
    return max(totals, default=0.0)


def sea_temperature(data: dict[str, SpotForecast], day: date) -> float | None:
    t = datetime.combine(day, time(9))
    vals = [h.sst for sf in data.values() for h in sf.hours if h.time == t and h.sst is not None]
    return mean(vals) if vals else None


def wetsuit(sst: float, config: Config) -> str:
    for t_min, name in config.alerts["wetsuit"]:
        if sst >= t_min:
            return name
    return config.alerts["wetsuit"][-1][1]


def summarize_day(data: dict[str, SpotForecast], config: Config, day: date,
                  rain_ref: datetime) -> DaySummary:
    verdicts = {s.id: evaluate_spot_day(data[s.id], s, config, day)
                for s in config.spots if s.id in data}
    best = {lv.id: best_for_level(verdicts, config, lv.id) for lv in config.levels}
    best = {k: v for k, v in best.items() if v is not None}
    top = max(RANK[v.verdict] for v in best.values())
    if top >= RANK[YELLOW]:
        # Día bueno: titula el nivel más bajo que alcanza el mejor color.
        headline = next(best[lv.id] for lv in config.levels
                        if lv.id in best and RANK[best[lv.id].verdict] == top)
    else:
        # Día malo para todos: titula el nivel más bajo, el más prudente (⛔ antes que 🔴).
        headline = next(best[lv.id] for lv in config.levels if lv.id in best)
    notify = any(verdicts[s.id][lv.id].notify
                 for s in config.spots if s.id in verdicts
                 for lv in config.levels if s.allows(lv.id))
    cleanup = headline.good and is_cleanup_day(
        data[headline.spot_id], config.spot(headline.spot_id), headline, config)
    beaches = [beach_day(s, verdicts[s.id], config) for s in config.spots if s.id in verdicts]
    return DaySummary(day=day, verdicts=verdicts, best=best, headline=headline, notify=notify,
                      cleanup=cleanup, rain_mm=rain_48h(data, config, rain_ref),
                      sst=sea_temperature(data, day), beaches=beaches)
