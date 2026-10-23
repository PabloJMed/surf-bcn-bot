"""Estado del último envío (data/state.json) e histórico de calibración (data/history.csv)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .score import DaySummary

HISTORY_FIELDS = [
    "run_date", "day", "spot", "level", "heff", "wave_height", "period", "t_estimated",
    "wave_dir", "wind_kmh", "wind_dir", "wind_kind", "score", "verdict", "window",
    "reason", "notify",
]


def load_state(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def should_send(now: datetime, last_sent: str | None, *, force: bool = False,
                send_after: str = "06:30", tz: str = "Europe/Madrid") -> tuple[bool, str]:
    """Envía en la primera ejecución con hora local ≥ send_after que no haya enviado ya hoy.

    `now` debe llevar zona horaria (en Actions, UTC). Así los dos crons UTC cubren
    el horario de verano y el de invierno.
    """
    local = now.astimezone(ZoneInfo(tz))
    today = local.date().isoformat()
    if force:
        return True, "forzado"
    if local.time() < time.fromisoformat(send_after):
        return False, f"demasiado pronto ({local:%H:%M} en Madrid)"
    if last_sent == today:
        return False, "ya enviado hoy"
    return True, "primera ejecución del día"


def history_rows(run_date: str, summaries: list[DaySummary], config: Config) -> list[dict]:
    rows = []
    for s in summaries:
        for spot in config.spots:
            for level in config.levels:
                dv = s.verdicts.get(spot.id, {}).get(level.id)
                if dv is None:
                    continue
                rows.append({
                    "run_date": run_date,
                    "day": s.day.isoformat(),
                    "spot": spot.id,
                    "level": level.id,
                    "heff": f"{dv.heff:.2f}",
                    "wave_height": f"{dv.wave_height:.2f}",
                    "period": f"{dv.period:.1f}",
                    "t_estimated": int(dv.t_estimated),
                    "wave_dir": round(dv.wave_direction),
                    "wind_kmh": round(dv.wind_speed),
                    "wind_dir": round(dv.wind_direction),
                    "wind_kind": dv.wind_kind,
                    "score": f"{dv.score:.2f}",
                    "verdict": dv.verdict,
                    "window": f"{dv.window[0]}-{dv.window[1]}" if dv.window else "",
                    "reason": dv.reason,
                    "notify": int(dv.notify),
                })
    return rows


def write_history(path: Path, rows: list[dict]) -> None:
    """Añade las filas; si ya había filas de la misma run_date (reenvío forzado), las sustituye."""
    path = Path(path)
    run_dates = {r["run_date"] for r in rows}
    kept = []
    if path.exists():
        with path.open(newline="") as f:
            kept = [r for r in csv.DictReader(f) if r.get("run_date") not in run_dates]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        w.writerows(kept + rows)
