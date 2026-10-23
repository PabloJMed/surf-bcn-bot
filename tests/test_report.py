import json
import re
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from surfbot import report
from surfbot.config import load_config
from surfbot.fetch import RawData, parse
from surfbot.report import MAX_LEN, NIGHT, NO_STAR, build_messages, failure_message
from surfbot.score import summarize_day

from tests.test_score import DAY, MAR, TERRAL, forecast

CFG = load_config()
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_TOKEN = "1234567890:" + "A" * 35   # con forma de token real, sin literal en el repo


def summary(rows, day=DAY):
    data = {s.id: forecast(s, rows, day) for s in CFG.spots}
    return summarize_day(data, CFG, day, datetime(day.year, day.month, day.day, 7))


def text(s, tomorrow=None):
    """Texto del (primer) mensaje de hoy."""
    return build_messages(s, tomorrow, CFG)[0].text


def plain(s):
    return re.sub(r"<[^>]+>", "", s)


# (hora, H, T, dir viento, km/h, dir ola)
GOOD = [(h, 0.75, 7, TERRAL, 8, 140) for h in range(9, 14)]
FLAT = [(h, 0.15, 5, TERRAL, 8, 140) for h in range(8, 18)]
BIG = [(h, 1.6, 8.5, TERRAL, 10, 140) for h in range(9, 18)]
SMALL = [(h, 0.3, 6, TERRAL, 8, 140) for h in range(9, 17)]


class TestHeaderAndSummary(unittest.TestCase):
    def test_header_has_no_beach_names(self):
        first = text(summary(GOOD)).splitlines()[0]
        self.assertIn("Surf BCN", first)
        self.assertIn("HOY", first)
        for sp in CFG.spots:
            self.assertNotIn(sp.short, first)

    def test_summary_lists_every_beach_north_to_south(self):
        lines = plain(text(summary(GOOD))).splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("Resumen"))
        rows = lines[start + 1:start + 1 + len(CFG.spots)]
        for sp, row in zip(CFG.spots, rows):
            self.assertIn(sp.short, row)
            self.assertTrue(row.startswith("⭐") or row.startswith(NO_STAR), row)

    def test_stars_match_the_summary(self):
        s = summary(GOOD)
        msg = plain(text(s))
        for b in s.beaches:
            name = CFG.spot(b.spot_id).short
            line = next(l for l in msg.splitlines() if l.endswith(name) or f" {name} ·" in l)
            self.assertEqual(line.count("⭐"), b.stars, name)


class TestNoBeach(unittest.TestCase):
    def test_custom_message_without_detail(self):
        msgs = build_messages(summary(FLAT), summary(FLAT, date(2026, 10, 24)), CFG)
        self.assertEqual(len(msgs), 2)
        for msg, day in zip(msgs, ("Hoy", "Mañana")):
            self.assertIn(f"{day} no merece la pena en ninguna playa", msg.text)
            self.assertNotIn("Resumen", msg.text)
            self.assertNotIn("━", msg.text)
            self.assertFalse(msg.notify)
            self.assertIn("Datos: Open-Meteo.com", msg.text)

    def test_courtesy_yellow_gives_no_stars(self):
        # 0,3 m: para avanzado es 🟡 "longboard" en el modelo acumulativo, pero no es un día real.
        s = summary(SMALL)
        self.assertEqual(s.best["avanzado"].verdict, "yellow")
        self.assertTrue(all(b.stars == 0 for b in s.beaches))
        self.assertIn("no merece la pena en ninguna playa", text(s))


class TestDetail(unittest.TestCase):
    def test_detail_only_for_starred_beaches(self):
        s = summary(GOOD)
        msg = text(s)
        self.assertEqual(msg.count("━━━━━━━━━━━━"), len(s.starred))
        for b in s.starred:
            self.assertIn(f"<b>{CFG.spot(b.spot_id).short}</b> · mejor de", msg)
        for b in s.beaches:
            if b.stars == 0:
                self.assertNotIn(f"<b>{CFG.spot(b.spot_id).short}</b> · mejor de", msg)

    def test_strip_always_covers_7_to_19(self):
        msg = text(summary(GOOD))
        strips = [l for l in msg.splitlines() if l.startswith("<code>")]
        self.assertTrue(strips)
        hours = CFG.general["strip_to"] - CFG.general["strip_from"]
        for strip in strips:
            self.assertIn(" 7h", strip)
            self.assertIn("19h", strip)
            dots = re.findall("🟢|🟡|🔴|⚪|⛔|" + NIGHT, strip)
            self.assertEqual(len(dots), hours)

    def test_bands_say_when_and_why_it_gets_worse(self):
        rows = GOOD + [(h, 0.75, 7, MAR, 25, 140) for h in range(14, 18)]
        msg = plain(text(summary(rows)))
        bands = next(l for l in msg.splitlines() if l.startswith("🟢"))
        self.assertIn("🟢 9–14 h", bands)
        self.assertIn("🔴 14–18 h: viento de mar", bands)

    def test_levels_and_boards(self):
        msg = plain(text(summary(GOOD)))
        line = next(l for l in msg.splitlines() if l.startswith("🏄 "))
        self.assertIn("Principiante", line)
        self.assertIn("softboard", line)

    def test_webcams_for_every_starred_beach_only(self):
        s = summary(GOOD)
        msg = text(s)
        starred = {b.spot_id for b in s.starred}
        for sp in CFG.spots:
            if sp.webcam_url:
                self.assertEqual(sp.webcam_url in msg, sp.id in starred, sp.id)

    def test_big_day_is_for_advanced_only(self):
        s = summary(BIG)
        msg = plain(text(s))
        self.assertTrue(s.starred)
        for b in s.starred:
            self.assertEqual(b.top_levels, ["avanzado"])
        self.assertIn("Principiante ⛔", msg)

    def test_garraf_never_recommended_to_beginners(self):
        rows = [(h, 0.75, 7, 345, 6, 165) for h in range(9, 15)]   # de frente para Garraf
        msg = plain(text(summary(rows)))
        garraf = msg.split("Garraf · mejor de")[1].split("━")[0] if "Garraf · mejor de" in msg else ""
        self.assertNotIn("Principiante", garraf)


class TestMessages(unittest.TestCase):
    def test_one_message_per_day_with_its_own_sound(self):
        good, flat = summary(GOOD), summary(FLAT, date(2026, 10, 24))
        hoy, manana = build_messages(flat, good, CFG)
        self.assertIn("<b>HOY</b>", hoy.text)
        self.assertIn("<b>MAÑANA</b>", manana.text)
        self.assertFalse(hoy.notify)
        self.assertTrue(manana.notify)

    def test_long_day_is_split_without_cutting_a_beach(self):
        s = summary(GOOD)
        with mock.patch.object(report, "MAX_LEN", 900):
            msgs = report.day_messages("HOY", s, CFG)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(msgs[0].notify)
        self.assertTrue(all(not m.notify for m in msgs[1:]))
        self.assertIn("sigue", msgs[1].text)
        self.assertIn("Datos: Open-Meteo.com", msgs[-1].text)
        joined = "\n".join(m.text for m in msgs)
        self.assertEqual(joined.count("━━━━━━━━━━━━"), len(s.starred))

    def test_no_token_and_length(self):
        s = summary(GOOD)
        for msg in build_messages(s, s, CFG):
            self.assertNotIn(FAKE_TOKEN, msg.text)
            self.assertNotIn("api.telegram.org", msg.text)
            self.assertLessEqual(len(msg.text), MAX_LEN)

    def test_star_legend_at_the_end(self):
        msg = text(summary(GOOD))
        legend = msg.split("<blockquote expandable>")[1]
        self.assertTrue(legend.startswith("<b>¿Qué significan las estrellas?</b>"))
        for n in range(1, 6):
            self.assertIn("⭐" * n + " ", legend)
        self.assertIn("☆ No recomendable", legend)
        self.assertLess(msg.index("<blockquote"), msg.index("Datos: Open-Meteo"))
        self.assertNotIn("<blockquote", text(summary(FLAT)))   # sin estrellas no hace falta

    def test_failure_message(self):
        msg = failure_message(CFG, date(2026, 10, 23))
        self.assertIn("⚠️", msg.text)
        self.assertIn("no he podido", msg.text)


class TestFixtures(unittest.TestCase):
    """Datos reales de Open-Meteo guardados el 23/10/2026."""

    @classmethod
    def setUpClass(cls):
        load = lambda n: json.loads((FIXTURES / f"{n}.json").read_text())
        cls.data = parse(CFG, RawData(load("marine"), load("peak"), load("forecast")))

    def test_every_spot_has_complete_daylight_hours(self):
        from surfbot.score import daylight_hours
        for sid, sf in self.data.items():
            hours = daylight_hours(sf, DAY)
            self.assertGreaterEqual(len(hours), 9, sid)
            self.assertFalse(any(h.t_estimated for h in hours), sid)   # pico real de ECMWF

    def test_message_builds(self):
        s1 = summarize_day(self.data, CFG, DAY, datetime(2026, 10, 23, 7))
        s2 = summarize_day(self.data, CFG, date(2026, 10, 24), datetime(2026, 10, 24, 9))
        msgs = build_messages(s1, s2, CFG)
        self.assertIn("HOY", msgs[0].text)
        self.assertIn("MAÑANA", msgs[-1].text)
        for m in msgs:
            self.assertLessEqual(len(m.text), MAX_LEN)

    def test_peak_fallback(self):
        load = lambda n: json.loads((FIXTURES / f"{n}.json").read_text())
        data = parse(CFG, RawData(load("marine"), None, load("forecast")))
        h = next(h for h in data["barceloneta"].hours if h.wave_period is not None)
        self.assertTrue(h.t_estimated)
        self.assertAlmostEqual(h.peak_period, h.wave_period * CFG.rules["peak_factor"], places=1)


if __name__ == "__main__":
    unittest.main()
