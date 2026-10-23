"""Compone el mensaje de Telegram (HTML). Sin red."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

from .config import Config
from .score import (CALM, CROSS, EMOJI, GREEN, OFFSHORE, ONSHORE, RANK, LABEL, DaySummary, rank_key,
                    DayVerdict, compass, fmt_num, wetsuit)

MAX_LEN = 4096
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
ATTRIBUTION = "Datos: Open-Meteo.com (CC BY 4.0)"
LEGEND = "🟢 merece la pena · 🟡 justito · 🔴 no merece la pena · 🤔 dudoso"
WIND_HINT = {CALM: "en calma (mar liso)",
             OFFSHORE: "terral (de tierra, ordena la ola)",
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


def wind_text(dv: DayVerdict, long: bool = False) -> str:
    kind = WIND_HINT.get(dv.wind_kind, dv.wind_kind) if long else dv.wind_kind
    if dv.wind_kind == CALM:
        return f"💨 {'Viento ' if long else ''}{kind}, {round(dv.wind_speed)} km/h"
    if long:
        return f"💨 Viento {kind} del {compass(dv.wind_direction)}, {round(dv.wind_speed)} km/h"
    return f"💨 {kind} {compass(dv.wind_direction)} {round(dv.wind_speed)} km/h"


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


def other_spots(s: DaySummary, config: Config) -> list[DayVerdict]:
    """Las demás playas aptas para el nivel del titular que merecen un intento:
    no rojas y con una franja seguida de al menos window_min_hours horas."""
    h = s.headline
    found = []
    for sp in config.spots:
        if sp.id == h.spot_id or not sp.allows(h.level) or sp.id not in s.verdicts:
            continue
        dv = s.verdicts[sp.id][h.level]
        if dv.good and dv.window and not dv.single_hour:
            found.append(dv)
    return sorted(found, key=rank_key, reverse=True)


def spot_detail(dv: DayVerdict, config: Config) -> list[str]:
    """Detalle compacto de una playa: franja, tira horaria, ola, viento y qué la estropea."""
    t = fmt_num(dv.period) + (" s*" if dv.t_estimated else " s")
    lines = [
        f"📍 <b>{escape(config.spot(dv.spot_id).short)}</b> · {EMOJI[dv.verdict]} "
        f"{LABEL[dv.verdict].lower()}, de {dv.window[0]} a {dv.window[1]} h" + (" 🤔" if dv.doubtful else ""),
        timeline_strip(dv),
        f"🌊 {fmt_num(dv.heff)} m · {t} · del {compass(dv.wave_direction)} · " + wind_text(dv),
    ]
    return lines + outside_window(dv)


def webcams(s: DaySummary, config: Config) -> str:
    """Enlaces solo a las playas recomendadas ese día (incluidas las dudosas)."""
    ids: list[str] = []
    for dv in [s.headline] + [s.best[lv.id] for lv in config.levels if lv.id in s.best]:
        if dv.good and dv.spot_id not in ids:
            ids.append(dv.spot_id)
    links = [f'<a href="{escape(config.spot(i).webcam_url, quote=True)}">{escape(config.spot(i).short)}</a>'
             for i in ids if config.spot(i).webcam_url]
    return "📷 Webcams: " + " · ".join(links) if links else ""


def day_body(s: DaySummary, config: Config) -> list[str]:
    h = s.headline
    if not s.good:
        # Día malo: una sola línea.
        return [f"<b>{EMOJI[h.verdict]} {LABEL[h.verdict]}</b> · {escape(h.reason)}"]
    level = next(lv for lv in config.levels if lv.id == h.level)
    spot = config.spot(h.spot_id)
    t = fmt_num(h.period) + (" s*" if h.t_estimated else " s")
    lines = [
        f"<b>{EMOJI[h.verdict]} {LABEL[h.verdict]}</b>" + (" 🤔" if h.doubtful else ""),
        f"⭐ <b>Mejor playa: {escape(spot.short)}</b>, de {h.window[0]} a {h.window[1]} h"
        + (f" ({level.label.lower()})" if h.level != config.levels[0].id else ""),
        timeline_strip(h),
        f"🌊 Olas de {fmt_num(h.heff)} m · periodo {t} · entran del {compass(h.wave_direction)}",
        wind_text(h, long=True),
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
        lines += ["", f"<b>También se puede intentar</b> ({level.label.lower()})"]
        for dv in others:
            lines += [""] + spot_detail(dv, config)
    cams = webcams(s, config)
    if cams:
        lines += ["", cams]
    return lines


def day_message(title: str, s: DaySummary, config: Config) -> Message:
    """Un mensaje por día, con su propio aviso sonoro."""
    lines = [f"🏄 <b>{escape(config.general['title'])}</b> · <b>{title}</b> {fmt_date(s.day)}", ""]
    lines += day_body(s, config)
    lines.append("")
    if s.sst is not None:
        lines.append(f"🌡️ Agua {round(s.sst)} °C → {wetsuit(s.sst, config)}")
    if s.good:
        lines.append(f"<i>{LEGEND}</i>")
    lines.append(f"<i>{ATTRIBUTION}</i>")
    text = "\n".join(lines)
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 1] + "…"
    return Message(text, s.notify)


def build_messages(today: DaySummary, tomorrow: DaySummary | None, config: Config) -> list[Message]:
    msgs = [day_message("HOY", today, config)]
    if tomorrow is not None:
        msgs.append(day_message("MAÑANA", tomorrow, config))
    return msgs


def failure_message(config: Config, day: date) -> Message:
    text = (f"🏄 <b>{escape(config.general['title'])}</b> · {fmt_date(day)}\n\n"
            "⚠️ Hoy no he podido obtener la previsión (Open-Meteo no responde).\n"
            "Esto no quiere decir que esté flat: mira Windguru o surf-forecast.\n\n"
            f"<i>{ATTRIBUTION}</i>")
    return Message(text, notify=True)
