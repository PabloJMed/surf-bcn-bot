"""Compone el mensaje de Telegram (HTML). Sin red."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

from .config import Config
from .score import (CROSS, EMOJI, GREEN, OFFSHORE, ONSHORE, RANK, LABEL, DaySummary,
                    DayVerdict, compass, fmt_num, wetsuit)

MAX_LEN = 4096
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
ATTRIBUTION = "Datos: Open-Meteo.com (CC BY 4.0)"
LEGEND = "🟢 merece la pena · 🟡 justito · 🔴 no merece la pena · 🤔 dudoso"
WIND_HINT = {OFFSHORE: "terral (de tierra, ordena la ola)",
             CROSS: "cruzado",
             ONSHORE: "de mar (revuelve el agua)"}


@dataclass
class Message:
    text: str
    notify: bool          # False → disable_notification (llega sin sonido)


def fmt_date(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def hours_text(dv: DayVerdict) -> str:
    return f"{dv.window[0]}–{dv.window[1]} h" if dv.window else ""


def timeline_strip(dv: DayVerdict) -> str:
    """Una bola por hora de luz: así se ve de dónde sale la franja."""
    if not dv.timeline:
        return ""
    first, last = dv.timeline[0][0], dv.timeline[-1][0]
    dots = "".join(EMOJI[v] for _, v, _ in dv.timeline)
    return f"<code>{first:>2}h</code> {dots} <code>{last + 1}h</code>"


def outside_window(dv: DayVerdict) -> list[str]:
    """Qué pasa justo antes y justo después de la franja buena."""
    if not dv.window:
        return []
    out = []
    before = [t for t in dv.timeline if t[0] < dv.window[0]]
    after = [t for t in dv.timeline if t[0] >= dv.window[1]]
    if before:
        out.append(f"⏱️ Antes de las {dv.window[0]} h: {escape(before[-1][2])}")
    if after:
        out.append(f"⏱️ Desde las {dv.window[1]} h: {escape(after[0][2])}")
    return out


def _board_text(dv: DayVerdict, config: Config) -> str:
    level = next(lv for lv in config.levels if lv.id == dv.level)
    if dv.heff < level.heff_ideal_min and dv.level != config.levels[0].id:
        text = f"pequeño, mejor {dv.board}"
    else:
        text = dv.board
    big = config.boards[dv.level].get("big_warning")
    if big is not None and dv.heff > big:
        text += " · día grande, solo acompañado"
    return text


def level_line(dv: DayVerdict, config: Config) -> str:
    level = next(lv for lv in config.levels if lv.id == dv.level)
    head = f"• <b>{level.label}:</b> {EMOJI[dv.verdict]}"
    if not dv.good:
        return f"{head} {escape(dv.reason)}"
    parts = [f"{escape(config.spot(dv.spot_id).short)}, {hours_text(dv)}", _board_text(dv, config)]
    if dv.single_hour:
        parts.append("solo 1 h buena")
    return f"{head} " + " · ".join(parts) + (" 🤔" if dv.doubtful else "")


def other_spots(s: DaySummary, config: Config) -> str:
    """Las demás playas aptas para el nivel del titular, agrupadas por color."""
    h = s.headline
    groups: dict[str, list[str]] = {}
    for sp in config.spots:
        if sp.id == h.spot_id or not sp.allows(h.level) or sp.id not in s.verdicts:
            continue
        v = s.verdicts[sp.id][h.level].verdict
        groups.setdefault(v, []).append(escape(sp.short))
    parts = [f"{EMOJI[v]} {', '.join(names)}"
             for v, names in sorted(groups.items(), key=lambda kv: -RANK[kv[0]])]
    return " · ".join(parts)


def webcams(s: DaySummary, config: Config) -> str:
    """Enlaces solo a las playas recomendadas ese día (incluidas las dudosas)."""
    ids: list[str] = []
    for dv in [s.headline] + [s.best[lv.id] for lv in config.levels if lv.id in s.best]:
        if dv.good and dv.spot_id not in ids:
            ids.append(dv.spot_id)
    links = [f'<a href="{escape(config.spot(i).webcam_url, quote=True)}">{escape(config.spot(i).short)}</a>'
             for i in ids if config.spot(i).webcam_url]
    return "📷 Webcams: " + " · ".join(links) if links else ""


def day_block(title: str, s: DaySummary, config: Config) -> list[str]:
    h = s.headline
    if not s.good:
        # Día malo: una sola línea.
        return [f"<b>{title} · {EMOJI[h.verdict]} {LABEL[h.verdict]}</b> · {escape(h.reason)}"]
    level = next(lv for lv in config.levels if lv.id == h.level)
    spot = config.spot(h.spot_id)
    t = fmt_num(h.period) + (" s*" if h.t_estimated else " s")
    head = f"<b>{title} · {EMOJI[h.verdict]} {LABEL[h.verdict]}</b>" + (" 🤔" if h.doubtful else "")
    lines = [
        head,
        f"📍 <b>{escape(spot.short)}</b>, mejor de {h.window[0]} a {h.window[1]} h"
        + (f" ({level.label.lower()})" if h.level != config.levels[0].id else ""),
        timeline_strip(h),
        f"🌊 Olas de {fmt_num(h.heff)} m · periodo {t} · entran del {compass(h.wave_direction)}",
        f"💨 Viento {WIND_HINT.get(h.wind_kind, h.wind_kind)} del {compass(h.wind_direction)}, "
        f"{round(h.wind_speed)} km/h",
    ]
    lines += outside_window(h)
    if h.doubtful:
        lines.append("🤔 Al límite: mira la webcam antes de ir")
    if s.cleanup:
        lines.append("🧹 Día de limpieza: ayer hubo viento de mar y hoy se ordena")
    if s.rain_mm >= config.alerts["rain_mm_48h"]:
        lines.append(f"💩 {round(s.rain_mm)} mm de lluvia en 48 h: evita el baño y aléjate "
                     "de las desembocaduras (Llobregat, Besòs)")
    if h.t_estimated:
        lines.append("* periodo estimado: hoy no hay dato de pico")
    lines += ["", "<b>Por nivel</b>"]
    lines += [level_line(s.best[lv.id], config) for lv in config.levels if lv.id in s.best]
    others = other_spots(s, config)
    if others:
        lines += ["", f"<b>Otras playas</b> ({level.label.lower()}): {others}"]
    cams = webcams(s, config)
    if cams:
        lines.append(cams)
    return lines


def build_message(today: DaySummary, tomorrow: DaySummary | None, config: Config) -> Message:
    lines = [f"🏄 <b>{escape(config.general['title'])}</b> · {fmt_date(today.day)}", ""]
    lines += day_block("HOY", today, config)
    if tomorrow is not None:
        lines += ["", "━━━━━━━━━━", ""] + day_block("MAÑANA", tomorrow, config)
    lines.append("")
    if today.sst is not None:
        lines.append(f"🌡️ Agua {round(today.sst)} °C → {wetsuit(today.sst, config)}")
    lines.append(f"<i>{LEGEND}</i>")
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
