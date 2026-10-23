"""Carga de config.toml en estructuras tipadas."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass(frozen=True)
class Level:
    id: str
    label: str
    heff_danger: float
    wind_max: float
    gust_max: float
    flat_threshold: float
    t_peak_min: float
    onshore_veto: float
    heff_ideal_min: float
    size_curve: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Spot:
    id: str
    name: str
    short: str
    marine: tuple[float, float]
    wind: tuple[float, float]
    facing: float
    exposure: float
    levels: tuple[str, ...]
    overrides: dict
    note: str = ""
    webcam_url: str = ""

    def allows(self, level_id: str) -> bool:
        return level_id in self.levels


@dataclass(frozen=True)
class Config:
    general: dict
    api: dict
    rules: dict
    levels: tuple[Level, ...]      # ordenados de menor a mayor nivel
    boards: dict
    alerts: dict
    spots: tuple[Spot, ...]

    def spot(self, spot_id: str) -> Spot:
        for s in self.spots:
            if s.id == spot_id:
                return s
        raise KeyError(f"spot desconocido: {spot_id}")

    def level_at(self, level: Level, spot: Spot) -> Level:
        """El nivel con los overrides del spot aplicados."""
        over = spot.overrides.get(level.id)
        return replace(level, **over) if over else level


def _pairs(raw) -> tuple[tuple[float, float], ...]:
    return tuple((float(a), float(b)) for a, b in raw)


def parse_config(data: dict) -> Config:
    levels = tuple(
        Level(
            id=lid,
            label=lv["label"],
            heff_danger=lv["heff_danger"],
            wind_max=lv["wind_max"],
            gust_max=lv["gust_max"],
            flat_threshold=lv["flat_threshold"],
            t_peak_min=lv["t_peak_min"],
            onshore_veto=lv["onshore_veto"],
            heff_ideal_min=lv["heff_ideal_min"],
            size_curve=_pairs(lv["size_curve"]),
        )
        for lid, lv in data["levels"].items()
    )
    spots = tuple(
        Spot(
            id=sid,
            name=sp["name"],
            short=sp.get("short", sp["name"]),
            marine=tuple(sp["marine"]),
            wind=tuple(sp["wind"]),
            facing=float(sp["facing"]),
            exposure=float(sp.get("exposure", 1.0)),
            levels=tuple(sp["levels"]),
            overrides=sp.get("overrides", {}),
            note=sp.get("note", ""),
            webcam_url=sp.get("webcam_url", ""),
        )
        for sid, sp in data["spots"].items()
    )
    rules = dict(data["rules"])
    rules["period_curve"] = _pairs(rules["period_curve"])
    return Config(
        general=data["general"],
        api=data["api"],
        rules=rules,
        levels=levels,
        boards=data["boards"],
        alerts=data["alerts"],
        spots=spots,
    )


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    with open(path, "rb") as f:
        return parse_config(tomllib.load(f))
