"""Compone el mensaje de Telegram (HTML). Sin red."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

from .config import Config
from .score import EMOJI, LABEL, DaySummary, DayVerdict, compass, fmt_num, wetsuit

MAX_LEN = 4096
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
ATTRIBUTION = "Datos: Open-Meteo.com (CC BY 4.0)"


@dataclass
class Message:
    text: str
    notify: bool          # False → disable_notification (llega sin sonido)


def fmt_date(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def window_text(dv: DayVerdict) -> str:
    return f"{dv.window[0]}–{dv.window[1]} h" if dv.window else ""


def conditions_text(dv: DayVerdict) -> str:
    t = fmt_num(dv.period) + (" s*" if dv.t_estimated else " s")
    return (f"{fmt_num(dv.heff)} m · {t} · del {compass(dv.wave_direction)} · "
            f"{dv.wind_kind} {compass(dv.wind_direction)} {round(dv.wind_speed)} km/h")


def _level_line(dv: DayVerdict, headline: DayVerdict, config: Config) -> str:
    level = next(lv for lv in config.levels if lv.id == dv.level)
    parts = [f"{EMOJI[dv.verdict]} {level.label}"]
    if not dv.good:
        parts.append(escape(dv.reason))
        return " · ".join(parts)
    if dv.spot_id != headline.spot_id:
        parts.append(f"{escape(config.spot(dv.spot_id).short)} {window_text(dv)}")
    elif dv.window != headline.window:
        parts.append(window_text(dv))
    if dv.heff < level.heff_ideal_min and dv.level != config.levels[0].id:
        parts.append(f"flojo → {dv.board}")
    else:
        parts.append(dv.board)
    big = config.boards[dv.level].get("big_warning")
    if big is not None and dv.heff > big:
        parts.append("día grande para ti, solo acompañado")
    if dv.single_hour and dv is not headline:
        parts.append("solo 1 h")
    return " · ".join(parts) + (" 🤔" if dv.doubtful else "")


def _other_spots(s: DaySummary, config: Config, cells: dict) -> str:
    """Los demás spots aptos para el nivel del titular, agrupando los que comparten celda."""
    h = s.headline
    others = [s.verdicts[sp.id][h.level] for sp in config.spots
              if sp.id != h.spot_id and sp.allows(h.level) and sp.id in s.verdicts]
    groups: list[list[DayVerdict]] = []
    for dv in others:
        for g in groups:
            ref = g[0]
            if (cells.get(dv.spot_id) == cells.get(ref.spot_id) and dv.verdict == ref.verdict
                    and abs(dv.score - ref.score) < 0.5):
                g.append(dv)
                break
        else:
            groups.append([dv])
    items = []
    for g in groups:
        ref = g[0]
        names = "/".join(escape(config.spot(dv.spot_id).short) for dv in g)
        if ref.good:
            items.append(f"{names} {EMOJI[ref.verdict]} ({fmt_num(ref.heff)} m, {fmt_num(ref.period)} s)")
        else:
            items.append(f"{names} {EMOJI[ref.verdict]} {escape(ref.reason)}")
    return " · ".join(items)


def day_block(title: str, s: DaySummary, config: Config, cells: dict) -> list[str]:
    h = s.headline
    if not s.good:
        # Día malo: una sola línea.
        return [f"<b>{title}:</b> {EMOJI[h.verdict]} {LABEL[h.verdict]} · {escape(h.reason)}"]
    spot = config.spot(h.spot_id)
    head = f"<b>{title}:</b> {EMOJI[h.verdict]} <b>{escape(spot.short)}</b> · {window_text(h)}"
    if h.single_hour:
        head += " (solo 1 h)"
    if h.doubtful:
        head += " · 🤔 dudoso: mira la webcam antes de ir"
    lines = [head, "   " + conditions_text(h)]
    lines += ["   " + _level_line(s.best[lv.id], h, config) for lv in config.levels if lv.id in s.best]
    if s.cleanup:
        lines.append("   🧹 Día de limpieza tras el viento de mar de ayer")
    if s.rain_mm >= config.alerts["rain_mm_48h"]:
        lines.append(f"   💩 {round(s.rain_mm)} mm de lluvia en 48 h: evita el baño y aléjate "
                     "de las desembocaduras (Llobregat, Besòs)")
    if h.t_estimated:
        lines.append("   * periodo estimado: hoy no hay dato de pico")
    if spot.webcam_url:
        lines.append(f'   📷 <a href="{escape(spot.webcam_url, quote=True)}">webcam</a>')
    others = _other_spots(s, config, cells)
    if others:
        lines.append(others)
    return lines


def build_message(today: DaySummary, tomorrow: DaySummary | None, config: Config,
                  cells: dict) -> Message:
    lines = [f"🏄 <b>{escape(config.general['title'])}</b> · {fmt_date(today.day)}", ""]
    lines += day_block("HOY", today, config, cells)
    if tomorrow is not None:
        lines += [""] + day_block("MAÑANA", tomorrow, config, cells)
    lines.append("")
    if today.sst is not None:
        lines.append(f"🌡️ Agua {round(today.sst)} °C → {wetsuit(today.sst, config)}")
    lines.append(f"<i>{ATTRIBUTION}</i>")
    text = "\n".join(lines)
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 1] + "…"
    notify = today.notify or (tomorrow is not None and tomorrow.notify)
    return Message(text, notify)


def failure_message(config: Config, day: date) -> Message:
    text = (f"🏄 <b>{escape(config.general['title'])}</b> · {fmt_date(day)}\n\n"
            "⚠️ Hoy no he podido obtener la previsión (Open-Meteo no responde).\n"
            "Esto no quiere decir que esté flat: mira Windguru o surf-forecast.\n\n"
            f"<i>{ATTRIBUTION}</i>")
    return Message(text, notify=True)
