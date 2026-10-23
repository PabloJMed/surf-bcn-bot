"""Cliente mínimo de la Bot API de Telegram.

Nunca registra el token ni la URL de la API: en caso de error solo se informa del
código HTTP y de la `description` que devuelve Telegram.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class TelegramError(Exception):
    pass


def clean_token(raw: str | None) -> str:
    """Quita espacios, saltos de línea y las marcas invisibles que añaden algunos
    terminales al pegar (bracketed paste)."""
    if not raw:
        return ""
    return raw.replace("\x1b[200~", "").replace("\x1b[201~", "").strip().strip("'\"")


def token_problem(token: str) -> str | None:
    """Describe qué le pasa a un token mal formado, sin revelar su contenido."""
    if not token:
        return "está vacío"
    if any(c.isspace() for c in token):
        return "tiene espacios o saltos de línea dentro: copia solo el token"
    if ":" not in token:
        return f"no tiene ':' ({len(token)} caracteres): no parece un token de bot"
    if not TOKEN_RE.match(token):
        num, _, rest = token.partition(":")
        return (f"no tiene la forma de un token: {len(num)} cifras antes de ':' y "
                f"{len(rest)} caracteres después (lo normal es unas 10 y 35)")
    return None


def _call(token: str, method: str, payload: dict, *, opener=urllib.request.urlopen,
          timeout: float = 20) -> object:
    req = urllib.request.Request(
        f"{API}/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            desc = json.load(e).get("description", "")
        except Exception:
            desc = ""
        raise TelegramError(f"Telegram HTTP {e.code}: {desc}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or type(e).__name__
        raise TelegramError(f"Telegram no responde ({reason})") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram: {body.get('description', 'respuesta no válida')}")
    return body["result"]


def send_message(token: str, chat_id: str, text: str, *, silent: bool = False,
                 opener=urllib.request.urlopen) -> None:
    _call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }, opener=opener)


def get_bot_username(token: str, *, opener=urllib.request.urlopen) -> str:
    return _call(token, "getMe", {}, opener=opener).get("username", "")


def get_chat_ids(token: str, *, opener=urllib.request.urlopen) -> list[tuple[int, str]]:
    """Los chats que han escrito al bot, para obtener el chat_id propio."""
    updates = _call(token, "getUpdates", {}, opener=opener)
    seen: dict[int, str] = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
            seen[chat["id"]] = f"{name} ({chat.get('type', '')})"
    return list(seen.items())
