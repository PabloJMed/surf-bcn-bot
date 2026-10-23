import json
import unittest
from datetime import date, datetime
from pathlib import Path

from surfbot.config import load_config
from surfbot.fetch import RawData, parse
from surfbot.report import MAX_LEN, build_messages, failure_message
from surfbot.score import summarize_day

from tests.test_score import DAY, TERRAL, forecast

CFG = load_config()
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_TOKEN = "1234567890:" + "A" * 35   # con forma de token real, sin literal en el repo


def text(s, tomorrow=None):
    """Texto del mensaje de hoy."""
    return build_messages(s, tomorrow, CFG)[0].text


def summary(rows, day=DAY):
    data = {s.id: forecast(s, rows, day) for s in CFG.spots}
    return summarize_day(data, CFG, day, datetime(day.year, day.month, day.day, 7))


GOOD = [(h, 0.75, 7, TERRAL, 6, 140) for h in range(9, 14)]
FLAT = [(h, 0.15, 5, TERRAL, 6, 140) for h in range(9, 18)]
BIG = [(h, 1.6, 8.5, TERRAL, 10, 140) for h in range(9, 18)]


class TestMessage(unittest.TestCase):
    def test_bad_days_fit_in_one_line_and_are_silent(self):
        s = summary(FLAT)
        for msg in build_messages(s, s, CFG):
            lines = msg.text.splitlines()
            # cabecera, línea en blanco, UNA línea de veredicto, línea en blanco, pie
            self.assertIn("FLAT", lines[2])
            self.assertEqual(lines[3], "")
            self.assertNotIn("Por nivel", msg.text)
            self.assertFalse(msg.notify)

    def test_good_day_has_headline_and_three_levels(self):
        s = summary(GOOD)
        msg = build_messages(s, None, CFG)[0]
        self.assertTrue(msg.notify)
        self.assertIn("🟢", msg.text)
        for label in ("Principiante", "Intermedio", "Avanzado"):
            self.assertIn(label, msg.text)
        self.assertIn("softboard", msg.text)
        self.assertIn("Datos: Open-Meteo.com", msg.text)

    def test_big_day_is_for_advanced_only(self):
        s = summary(BIG)
        msg = build_messages(s, None, CFG)[0]
        line = next(l for l in msg.text.splitlines() if "Principiante" in l)
        self.assertIn("⛔", line)
        self.assertNotIn("Garraf", line)
        self.assertIn("Avanzado", msg.text)

    def test_garraf_never_in_beginner_line(self):
        rows = [(h, 0.75, 7, 345, 6, 165) for h in range(9, 15)]   # de frente para Garraf
        s = summary(rows)
        msg = build_messages(s, None, CFG)[0]
        line = next(l for l in msg.text.splitlines() if "Principiante" in l)
        self.assertNotIn("Garraf", line)
        self.assertNotIn("Garraf", "\n".join(msg.text.splitlines()[2:4]))   # ni en el titular

    def test_no_token_and_length(self):
        s = summary(GOOD)
        for msg in build_messages(s, s, CFG):
            self.assertNotIn(FAKE_TOKEN, msg.text)
            self.assertNotIn("api.telegram.org", msg.text)
            self.assertLessEqual(len(msg.text), MAX_LEN)

    def test_timeline_explains_the_window(self):
        # Buenas de 9 a 13 h; a partir de las 14 h entra viento de mar fuerte.
        rows = GOOD + [(h, 0.75, 7, 140, 25, 140) for h in range(14, 18)]
        s = summary(rows)
        lines = text(s).splitlines()
        strip = next(l for l in lines if l.startswith("<code>"))
        self.assertEqual(strip.count("🟢"), 5)
        self.assertEqual(strip.count("🔴"), 4)
        self.assertIn("Mejor playa: ", "\n".join(lines))
        self.assertIn(", de 9 a 14 h", "\n".join(lines))
        after = next(l for l in lines if l.startswith("⏱️ Desde las 14 h"))
        self.assertIn("viento de mar", after)

    def test_every_good_level_line_has_beach_and_hours(self):
        s = summary(GOOD)
        lines = [l for l in text(s).splitlines() if l.startswith("• <b>")]
        self.assertEqual(len(lines), 3)
        for l in lines:
            if "🟢" in l or "🟡" in l:
                self.assertRegex(l, r", \d+–\d+ h")

    def test_webcams_only_for_recommended_beaches(self):
        s = summary(GOOD)
        msg = text(s)
        recommended = {s.headline.spot_id} | {dv.spot_id for dv in s.best.values() if dv.good}
        for sp in CFG.spots:
            if sp.webcam_url:
                self.assertEqual(sp.webcam_url in msg, sp.id in recommended, sp.id)
        self.assertNotIn("Webcams", text(summary(FLAT)))

    def test_legend(self):
        self.assertIn("justito", text(summary(GOOD)))
        self.assertNotIn("justito", text(summary(FLAT)))   # un día malo no la necesita

    def test_one_message_per_day_with_its_own_sound(self):
        good, flat = summary(GOOD), summary(FLAT, date(2026, 10, 24))
        hoy, manana = build_messages(flat, good, CFG)
        self.assertIn("<b>HOY</b>", hoy.text)
        self.assertIn("<b>MAÑANA</b>", manana.text)
        self.assertFalse(hoy.notify)
        self.assertTrue(manana.notify)
        for msg in (hoy, manana):
            self.assertIn("Datos: Open-Meteo.com", msg.text)

    def test_other_beaches_only_with_a_decent_window(self):
        s = summary(GOOD)
        h = s.headline
        msg = text(s)
        section = ""
        if "También se puede intentar" in msg:
            section = msg.split("También se puede intentar")[1].split("📷")[0].split("🌡️")[0]
        for sp in CFG.spots:
            if sp.id == h.spot_id or not sp.allows(h.level):
                continue
            dv = s.verdicts[sp.id][h.level]
            shown = f"<b>{sp.short}</b>" in section
            self.assertEqual(shown, dv.good and not dv.single_hour, sp.id)
        # Cada playa listada trae su detalle: tira horaria y ola/viento.
        self.assertEqual(section.count("📍"), section.count("<code>") // 2)
        self.assertEqual(section.count("📍"), section.count("🌊"))

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
        self.assertEqual(len(msgs), 2)
        self.assertIn("HOY", msgs[0].text)
        self.assertIn("MAÑANA", msgs[1].text)
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
