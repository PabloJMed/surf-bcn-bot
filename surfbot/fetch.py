"""Descarga y normaliza los datos de Open-Meteo.

Tres llamadas, cada una con todos los spots a la vez:
  1. Marine sin `models=` (best_match): altura, dirección, periodo medio, swell, SST.
  2. Marine con `models=ecmwf_wam025`: periodo de pico (el único modelo que lo da aquí).
  3. Forecast: viento, rachas, lluvia, amanecer y atardecer.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

from .config import Config

MARINE_VARS = (
    "wave_height,wave_direction,wave_period,wave_peak_period,"
    "swell_wave_height,swell_wave_direction,swell_wave_period,"
    "wind_wave_height,wind_wave_period,sea_surface_temperature"
)
FORECAST_HOURLY = "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation"
FORECAST_DAILY = "sunrise,sunset,precipitation_sum"


class FetchError(Exception):
    pass


@dataclass
class Hour:
    time: datetime
    wave_height: float | None = None
    wave_direction: float | None = None
    wave_period: float | None = None      # periodo medio
    peak_period: float | None = None      # periodo de pico usado para puntuar
    t_estimated: bool = False             # True si el pico sale de medio × peak_factor
    swell_height: float | None = None
    swell_period: float | None = None
    sst: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    gusts: float | None = None
    precipitation: float | None = None

    def complete(self) -> bool:
        return None not in (
            self.wave_height, self.wave_direction, self.peak_period,
            self.wind_speed, self.wind_direction, self.gusts,
        )


@dataclass
class SpotForecast:
    spot_id: str
    marine_cell: tuple[float, float]
    hours: list[Hour]
    sun: dict[date, tuple[datetime, datetime]] = field(default_factory=dict)

    def hour_at(self, t: datetime) -> Hour | None:
        for h in self.hours:
            if h.time == t:
                return h
        return None


@dataclass
class RawData:
    marine: list
    peak: list | None
    forecast: list


def get_json(url: str, params: dict, *, attempts: int = 3, backoff: float = 1.0,
             timeout: float = 30, sleep=time.sleep, opener=urllib.request.urlopen):
    """GET con reintentos y backoff exponencial (backoff, 2·backoff, ...)."""
    full = url + "?" + urllib.parse.urlencode(params, safe=",")
    req = urllib.request.Request(full, headers={"User-Agent": "surf-bcn-bot"})
    last = None
    for i in range(attempts):
        try:
            with opener(req, timeout=timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            last = e
            if i < attempts - 1:
                sleep(backoff * 2 ** i)
    raise FetchError(f"{url}: {last}")


def _as_list(data) -> list:
    return data if isinstance(data, list) else [data]


def _coords(points) -> dict:
    return {
        "latitude": ",".join(f"{p[0]}" for p in points),
        "longitude": ",".join(f"{p[1]}" for p in points),
    }


def download(config: Config, http=get_json) -> RawData:
    """Las tres llamadas. Si falla la de pico, se sigue con el periodo medio estimado."""
    api = config.api
    opts = {"attempts": api["attempts"], "backoff": api["backoff_seconds"],
            "timeout": api["timeout_seconds"]}
    marine_pts = [s.marine for s in config.spots]
    wind_pts = [s.wind for s in config.spots]
    tz = config.general["timezone"]

    marine = http(api["marine_url"], {
        **_coords(marine_pts), "hourly": MARINE_VARS, "timezone": tz,
        "past_days": 1, "forecast_days": 3,
    }, **opts)
    forecast = http(api["forecast_url"], {
        **_coords(wind_pts), "hourly": FORECAST_HOURLY, "daily": FORECAST_DAILY,
        "timezone": tz, "past_days": 2, "forecast_days": 3, "wind_speed_unit": "kmh",
    }, **opts)
    try:
        peak = http(api["marine_url"], {
            **_coords(marine_pts), "hourly": "wave_peak_period", "models": api["peak_model"],
            "timezone": tz, "past_days": 1, "forecast_days": 3,
        }, **opts)
    except FetchError:
        peak = None
    return RawData(_as_list(marine), _as_list(peak) if peak is not None else None,
                   _as_list(forecast))


def _series(block: dict, key: str) -> dict[str, float | None]:
    times = block.get("time", [])
    values = block.get(key) or [None] * len(times)
    return dict(zip(times, values))


def _peak_key(hourly: dict) -> str | None:
    for k in hourly:
        if k.startswith("wave_peak_period"):
            return k
    return None


def parse(config: Config, raw: RawData) -> dict[str, SpotForecast]:
    """Une las tres respuestas por spot y hora. El orden de las listas es el de config.spots."""
    if len(raw.marine) != len(config.spots) or len(raw.forecast) != len(config.spots):
        raise FetchError("la respuesta no tiene un punto por spot")
    factor = config.rules["peak_factor"]
    out: dict[str, SpotForecast] = {}
    for i, spot in enumerate(config.spots):
        m = raw.marine[i]["hourly"]
        f = raw.forecast[i]["hourly"]
        fd = raw.forecast[i].get("daily", {})

        # Pico: primero el de ECMWF; si no, el de best_match (hoy vacío); si no, estimado.
        peak_series: dict = {}
        if raw.peak is not None and i < len(raw.peak):
            ph = raw.peak[i]["hourly"]
            key = _peak_key(ph)
            if key:
                peak_series = _series(ph, key)
        own_peak = _series(m, "wave_peak_period")

        wind = _series(f, "wind_speed_10m")
        wdir = _series(f, "wind_direction_10m")
        gust = _series(f, "wind_gusts_10m")
        rain = _series(f, "precipitation")

        hours = []
        for idx, ts in enumerate(m["time"]):
            def mv(k):
                vals = m.get(k)
                return vals[idx] if vals else None
            mean_t = mv("wave_period")
            peak = peak_series.get(ts)
            if peak is None:
                peak = own_peak.get(ts)
            estimated = False
            if peak is None and mean_t is not None:
                peak = round(mean_t * factor, 2)
                estimated = True
            hours.append(Hour(
                time=datetime.fromisoformat(ts),
                wave_height=mv("wave_height"),
                wave_direction=mv("wave_direction"),
                wave_period=mean_t,
                peak_period=peak,
                t_estimated=estimated,
                swell_height=mv("swell_wave_height"),
                swell_period=mv("swell_wave_period"),
                sst=mv("sea_surface_temperature"),
                wind_speed=wind.get(ts),
                wind_direction=wdir.get(ts),
                gusts=gust.get(ts),
                precipitation=rain.get(ts),
            ))
        # La lluvia de las horas pasadas que la API marina no cubre (past_days=2 vs 1).
        marine_times = set(m["time"])
        extra = [Hour(time=datetime.fromisoformat(ts), precipitation=v)
                 for ts, v in rain.items() if ts not in marine_times]
        hours = sorted(hours + extra, key=lambda h: h.time)

        sun = {}
        for d, rise, set_ in zip(fd.get("time", []), fd.get("sunrise", []), fd.get("sunset", [])):
            if rise and set_:
                sun[date.fromisoformat(d)] = (datetime.fromisoformat(rise),
                                              datetime.fromisoformat(set_))
        out[spot.id] = SpotForecast(
            spot_id=spot.id,
            marine_cell=(raw.marine[i]["latitude"], raw.marine[i]["longitude"]),
            hours=hours,
            sun=sun,
        )
    return out


def fetch(config: Config, http=get_json) -> dict[str, SpotForecast]:
    return parse(config, download(config, http))
