"""CLI del bot.

  python -m surfbot --dry-run            descarga e imprime el mensaje sin enviarlo
  python -m surfbot --send [--force]     envía (requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)
  python -m surfbot --get-chat-id        lista los chat_id que han escrito al bot
  python -m surfbot --explain <spot>     tabla hora a hora, por nivel, para calibrar
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_CONFIG, ROOT, Config, load_config
from .fetch import FetchError, SpotForecast, fetch
from .report import Message, build_message, failure_message
from .score import (EMOJI, Conditions, compass, daylight_hours, fmt_num, score_hour,
                    summarize_day)
from .state import history_rows, load_state, save_state, should_send, write_history
from .telegram import (TelegramError, clean_token, get_bot_username, get_chat_ids,
                       send_message, token_problem)

DATA = ROOT / "data"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Lee un .env local (ignorado por git) sin pisar variables ya definidas."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def now_local(config: Config) -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(config.general["timezone"]))


def summaries(data: dict[str, SpotForecast], config: Config, now: datetime):
    today = now.date()
    tomorrow = today + timedelta(days=1)
    naive_now = now.replace(tzinfo=None)
    s_today = summarize_day(data, config, today, naive_now)
    s_tomorrow = summarize_day(data, config, tomorrow, datetime.combine(tomorrow, time(9)))
    return s_today, s_tomorrow


def compose(config: Config, now: datetime) -> tuple[Message, list | None]:
    """Mensaje y, si hubo datos, los resúmenes del día (para el histórico)."""
    try:
        data = fetch(config)
    except FetchError as e:
        print(f"Error al descargar la previsión: {e}", file=sys.stderr)
        return failure_message(config, now.date()), None
    s_today, s_tomorrow = summaries(data, config, now)
    cells = {sid: sf.marine_cell for sid, sf in data.items()}
    return build_message(s_today, s_tomorrow, config, cells), [s_today, s_tomorrow]


def cmd_dry_run(config: Config) -> int:
    msg, _ = compose(config, now_local(config))
    print(msg.text)
    print(f"\n--- {len(msg.text)} caracteres · aviso {'con sonido' if msg.notify else 'sin sonido'} ---")
    return 0


def cmd_send(config: Config, force: bool) -> int:
    token = clean_token(os.environ.get("TELEGRAM_BOT_TOKEN"))
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    missing = [name for name, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)) if not v]
    if missing:
        print(f"Falta o está vacío en el entorno: {', '.join(missing)}.", file=sys.stderr)
        return 2
    state_path = DATA / "state.json"
    state = load_state(state_path)
    ok, why = should_send(datetime.now(timezone.utc), state.get("last_sent"), force=force,
                          send_after=config.general["send_after"], tz=config.general["timezone"])
    if not ok:
        print(f"No se envía: {why}.")
        return 0
    now = now_local(config)
    msg, sums = compose(config, now)
    try:
        send_message(token, chat_id, msg.text, silent=not msg.notify)
    except TelegramError as e:
        print(f"No se pudo enviar: {e}", file=sys.stderr)
        return 1
    today = now.date().isoformat()
    save_state(state_path, {"last_sent": today})
    if sums:
        write_history(DATA / "history.csv", history_rows(today, sums, config))
    print(f"Enviado ({why}, aviso {'con sonido' if msg.notify else 'sin sonido'}).")
    return 0


def cmd_get_chat_id() -> int:
    token = clean_token(os.environ.get("TELEGRAM_BOT_TOKEN"))
    if not token:
        print("Exporta TELEGRAM_BOT_TOKEN en tu terminal antes de ejecutar esto.", file=sys.stderr)
        return 2
    problem = token_problem(token)
    if problem:
        print(f"El token {problem}.", file=sys.stderr)
        print("Cópialo otra vez desde @BotFather: /mybots → tu bot → API Token.", file=sys.stderr)
        return 2
    try:
        chats = get_chat_ids(token)
    except TelegramError as e:
        print(e, file=sys.stderr)
        if "401" in str(e):
            print("El token tiene buena forma pero Telegram no lo reconoce. Suele ser porque está\n"
                  "incompleto o porque después se generó otro en @BotFather (el anterior deja de\n"
                  "valer). Cópialo de nuevo desde @BotFather: /mybots → tu bot → API Token.",
                  file=sys.stderr)
        return 1
    if not chats:
        try:
            user = get_bot_username(token)
        except TelegramError:
            user = ""
        bot = f"@{user} (https://t.me/{user})" if user else "tu bot"
        print(f"No hay mensajes. Escribe /start a {bot} en Telegram y vuelve a probar.")
    for cid, name in chats:
        print(f"{cid}\t{name}")
    return 0


def cmd_explain(config: Config, spot_id: str, level_filter: str | None) -> int:
    try:
        spot = config.spot(spot_id)
    except KeyError:
        print(f"Spot desconocido. Opciones: {', '.join(s.id for s in config.spots)}", file=sys.stderr)
        return 2
    data = fetch(config)
    sf = data[spot.id]
    now = now_local(config)
    print(f"{spot.name} · facing {spot.facing:.0f}° · terral {(spot.facing + 180) % 360:.0f}° · "
          f"celda marina {sf.marine_cell} · niveles aptos: {', '.join(spot.levels)}")
    for day in (now.date(), now.date() + timedelta(days=1)):
        hours = daylight_hours(sf, day)
        scored = [(h, score_hour(Conditions.from_hour(h), spot, config)) for h in hours]
        for i, level in enumerate(config.levels):
            if level_filter and level.id != level_filter:
                continue
            print(f"\n{day:%a %d %b} · {level.label}")
            print(" hora  Heff  T pico  ola   viento               disp  notif  veredicto · motivo")
            for h, ss in scored:
                s = ss[i]
                t = fmt_num(h.peak_period) + ("*" if h.t_estimated else "")
                wind = f"{s.wind_kind} {compass(h.wind_direction)} {round(h.wind_speed)}/{round(h.gusts)}"
                flag = " 🤔" if s.doubtful else ""
                print(f" {h.time:%H}h  {s.heff:4.2f}  {t:>6}  {compass(h.wave_direction):>3}  "
                      f"{wind:<19}  {s.display:4.1f}  {s.notify:5.1f}  {EMOJI[s.verdict]}{flag} {s.reason}")
    print("\n* periodo estimado (medio × peak_factor). disp: puntuación del color. "
          "notif: la que decide el sonido.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="surfbot", description="Previsión de surf BCN → Sitges.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="imprime el mensaje sin enviarlo")
    g.add_argument("--send", action="store_true", help="envía por Telegram")
    g.add_argument("--get-chat-id", action="store_true", help="lista los chat_id del bot")
    g.add_argument("--explain", metavar="SPOT", help="tabla hora a hora de un spot")
    p.add_argument("--force", action="store_true", help="con --send: envía aunque ya se haya enviado hoy")
    p.add_argument("--level", help="con --explain: solo este nivel")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="ruta de config.toml")
    args = p.parse_args(argv)

    load_dotenv()
    config = load_config(args.config)
    if args.dry_run:
        return cmd_dry_run(config)
    if args.send:
        return cmd_send(config, args.force)
    if args.get_chat_id:
        return cmd_get_chat_id()
    return cmd_explain(config, args.explain, args.level)


if __name__ == "__main__":
    sys.exit(main())
