"""Compone los mensajes de Telegram (HTML), uno por día. Sin red.

Estructura de cada mensaje:
  1. Cabecera: Surf BCN, día y fecha.
  2. Resumen: todas las playas de norte a sur con sus estrellas (☆ = no recomendable).
  3. Desglose de las playas con al menos una estrella: tira de 7 a 19 h, franjas por color,
     ola, viento, niveles y webcam.
  Si ninguna playa tiene estrellas, un mensaje corto que lo dice, sin desglose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

from .config import Config
from .score import (CALM, CROSS, DANGER, EMOJI, FLAT, GREEN, OFFSHORE, ONSHORE, RED, YELLOW,
                    BeachDay, DaySummary, DayVerdict, compass, fmt_num, wetsuit)

MAX_LEN = 4096
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
ATTRIBUTION = "Datos: Open-Meteo.com (CC BY 4.0)"
NIGHT = "🌙"
NO_STAR = "☆"
LEGEND = ("⭐ se puede intentar · ⭐⭐ o más, buenas condiciones · ⭐⭐⭐⭐⭐ día idílico · "
          "☆ no recomendable\n"
          "Horas: 🟢 buenas · 🟡 justas · 🔴 malas · ⚪ sin olas · ⛔ peligro · 🌙 de noche")
WIND_HINT = {CALM: "calma", OFFSHORE: "terral", CROSS: "cruzado", ONSHORE: "de mar"}


@dataclass
class Message:
    text: str
    notify: bool          # False → disable_notification (llega sin sonido)


def fmt_date(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def stars_text(n: int) -> str:
    return "⭐" * n if n > 0 else NO_STAR


def hours_text(dv: DayVerdict) -> str:
    w = dv.star_window
    return f"{w[0]}–{w[1]} h" if w else ""


def _level(config: Config, level_id: str):
    return next(lv for lv in config.levels if lv.id == level_id)


# --- Franjas horarias ---------------------------------------------------------------

def hourly(dv: DayVerdict, config: Config) -> list[tuple[int, str | None, str]]:
    """(hora, veredicto o None si es de noche, motivo) para cada hora de la tira."""
    by_hour = {hh: (v, r) for hh, v, r in dv.timeline}
    out = []
    for hh in range(config.general["strip_from"], config.general["strip_to"]):
        v, r = by_hour.get(hh, (None, ""))
        out.append((hh, v, r))
    return out


def timeline_strip(dv: DayVerdict, config: Config) -> str:
    """Una bola por hora, de 7 a 19 h. Siempre la misma escala para aprender a leerla."""
    dots = "".join(EMOJI[v] if v else NIGHT for _, v, _ in hourly(dv, config))
    return (f"<code>{config.general['strip_from']:>2}h</code> {dots} "
            f"<code>{config.general['strip_to']}h</code>")


def _join_ranges(ranges: list[tuple[int, int]]) -> str:
    parts = [f"{a}–{b}" for a, b in ranges]
    return (", ".join(parts[:-1]) + " y " + parts[-1] if len(parts) > 1 else parts[0]) + " h"


def bands_text(dv: DayVerdict, config: Config) -> str:
    """Las horas agrupadas por color, de mejor a peor, con el motivo de las malas."""
    runs: list[list] = []                       # [veredicto, desde, hasta, motivo]
    for hh, v, r in hourly(dv, config):
        if v is None:
            continue
        if runs and runs[-1][0] == v and runs[-1][2] == hh:
            runs[-1][2] = hh + 1
        else:
            runs.append([v, hh, hh + 1, r])
    parts = []
    end = dv.star_window[1] if dv.star_window else 0
    for v in (GREEN, YELLOW, RED, FLAT, DANGER):
        mine = [run for run in runs if run[0] == v]
        if not mine:
            continue
        text = f"{EMOJI[v]} {_join_ranges([(a, b) for _, a, b, _ in mine])}"
        if v in (RED, FLAT, DANGER):
            # El motivo que importa es el que cierra la franja buena; si no, el primero.
            after = [run for run in mine if run[1] >= end] or mine
            text += f": {escape(after[0][3])}"
        parts.append(text)
    return " · ".join(parts)


# --- Bloques ------------------------------------------------------------------------

def summary_lines(s: DaySummary, config: Config) -> list[str]:
    lines = ["<b>Resumen</b> · mejor franja de cada playa"]
    for b in s.beaches:
        name = escape(config.spot(b.spot_id).short)
        if b.stars == 0:
            lines.append(f"{NO_STAR} {name}")
            continue
        line = f"{stars_text(b.stars)} <b>{name}</b> · {hours_text(b.best)}"
        allowed = [dv.level for dv in b.levels]
        if b.top_levels != allowed:
            line += " · " + ", ".join(_level(config, l).label.lower() for l in b.top_levels)
        lines.append(line)
    return lines


def _board_text(dv: DayVerdict, config: Config) -> str:
    level = _level(config, dv.level)
    text = dv.board
    if dv.heff < level.heff_ideal_min and dv.level != config.levels[0].id:
        text += " (pequeño)"
    big = config.boards[dv.level].get("big_warning")
    if big is not None and dv.heff > big:
        text += ", día grande: solo acompañado"
    return text


def levels_line(b: BeachDay, config: Config) -> str:
    parts = []
    for dv in b.levels:
        label = _level(config, dv.level).label
        if dv.stars > 0:
            parts.append(f"{label} {stars_text(dv.stars)} {_board_text(dv, config)}")
        elif dv.verdict == DANGER:
            parts.append(f"{label} ⛔ demasiado para tu nivel")
        elif dv.heff < _level(config, dv.level).heff_ideal_min:
            parts.append(f"{label} {NO_STAR} pequeño")
        else:
            parts.append(f"{label} {NO_STAR}")
    return "🏄 " + " · ".join(parts)


def beach_detail(b: BeachDay, config: Config) -> list[str]:
    dv = b.best
    spot = config.spot(b.spot_id)
    t = fmt_num(dv.period) + (" s*" if dv.t_estimated else " s")
    wind = (f"💨 calma, {round(dv.wind_speed)} km/h" if dv.wind_kind == CALM else
            f"💨 {WIND_HINT.get(dv.wind_kind, dv.wind_kind)} del {compass(dv.wind_direction)}, "
            f"{round(dv.wind_speed)} km/h")
    lines = [
        f"{stars_text(b.stars)} <b>{escape(spot.short)}</b> · mejor de "
        f"{dv.star_window[0]} a {dv.star_window[1]} h",
        timeline_strip(dv, config),
        bands_text(dv, config),
        f"🌊 {fmt_num(dv.heff)} m · {t} · del {compass(dv.wave_direction)} · {wind}",
        levels_line(b, config),
    ]
    if dv.t_estimated:
        lines.append("* periodo estimado: hoy no hay dato de pico")
    if spot.webcam_url:
        lines.append(f'📷 <a href="{escape(spot.webcam_url, quote=True)}">webcam de {escape(spot.short)}</a>')
    return lines


def day_messages(title: str, s: DaySummary, config: Config) -> list[Message]:
    """Los mensajes de un día: normalmente uno. Si el desglose no cabe en el límite de
    Telegram, sigue en otro mensaje (sin sonido), sin cortar nunca a mitad de playa."""
    header = f"🏄🌊 <b>{escape(config.general['title'])}</b> · <b>{title}</b> {fmt_date(s.day)}"
    starred = s.starred
    head = [header, ""]
    blocks: list[list[str]] = []
    if not starred:
        # Ninguna playa recomendable: lo decimos y ya, sin desglose.
        h = s.headline
        head += [f"😴 <b>{title.capitalize()} no merece la pena en ninguna playa</b>",
                 f"{EMOJI[h.verdict]} {escape(h.reason)}"]
    else:
        head += summary_lines(s, config)
        if s.cleanup:
            head.append("🧹 Día de limpieza: ayer hubo viento de mar y hoy se ordena")
        if s.rain_mm >= config.alerts["rain_mm_48h"]:
            head.append(f"💩 {round(s.rain_mm)} mm de lluvia en 48 h: evita el baño y aléjate "
                        "de las desembocaduras (Llobregat, Besòs)")
        blocks = [["", "━━━━━━━━━━━━"] + beach_detail(b, config) for b in starred]
    foot = [""]
    if s.sst is not None:
        foot.append(f"🌡️ Agua {round(s.sst)} °C → {wetsuit(s.sst, config)}")
    if starred:
        foot.append(f"<i>{LEGEND}</i>")
    foot.append(f"<i>{ATTRIBUTION}</i>")

    parts: list[list[str]] = [head]
    for block in blocks:
        if len("\n".join(parts[-1] + block + foot)) > MAX_LEN:
            parts.append([f"{header} · sigue", ""])
        parts[-1] += block
    parts[-1] += foot
    return [Message("\n".join(p), s.notify and i == 0) for i, p in enumerate(parts)]


def build_messages(today: DaySummary, tomorrow: DaySummary | None, config: Config) -> list[Message]:
    msgs = day_messages("HOY", today, config)
    if tomorrow is not None:
        msgs += day_messages("MAÑANA", tomorrow, config)
    return msgs


def failure_message(config: Config, day: date) -> Message:
    text = (f"🏄🌊 <b>{escape(config.general['title'])}</b> · {fmt_date(day)}\n\n"
            "⚠️ Hoy no he podido obtener la previsión (Open-Meteo no responde).\n"
            "Esto no quiere decir que esté flat: mira Windguru o surf-forecast.\n\n"
            f"<i>{ATTRIBUTION}</i>")
    return Message(text, notify=True)
