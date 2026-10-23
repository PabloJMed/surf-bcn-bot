import itertools
import unittest
from datetime import date, datetime, timedelta

from surfbot.config import load_config
from surfbot.fetch import Hour, SpotForecast
from surfbot.score import (
    DANGER, FLAT, GREEN, RANK, RED, YELLOW, Conditions, angle_diff, board_for,
    danger_ceiling, day_verdict, dir_factor, interp, score_hour, summarize_day,
)

CFG = load_config()
BCN = CFG.spot("barceloneta")          # facing 135, terral 315
TERRAL, MAR, NE, E = 315, 135, 45, 90
LEVELS = [lv.id for lv in CFG.levels]  # principiante, intermedio, avanzado


def hour(h, t, wind_dir, wind, *, wave_dir=135, gusts=None, spot=BCN):
    """Puntúa una hora en un spot. Sin bonus de swell. Devuelve {nivel: HourScore}."""
    c = Conditions(h, wave_dir, t, wind, wind_dir, gusts if gusts is not None else wind)
    return {s.level: s for s in score_hour(c, spot, CFG)}


class TestBasics(unittest.TestCase):
    def test_angle_diff(self):
        self.assertEqual(angle_diff(350, 10), 20)
        self.assertEqual(angle_diff(135, 315), 180)

    def test_interp_clamps(self):
        pts = ((0, 0), (10, 10))
        self.assertEqual(interp(-5, pts), 0)
        self.assertEqual(interp(5, pts), 5)
        self.assertEqual(interp(50, pts), 10)

    def test_dir_factor(self):
        self.assertEqual(dir_factor(135, 135), 1.0)
        self.assertAlmostEqual(dir_factor(90, 135), 0.85)   # Δ 45
        self.assertAlmostEqual(dir_factor(65, 135), 0.5667, places=3)
        self.assertEqual(dir_factor(0, 135), 0.0)            # Δ 135: sombra

    def test_danger_ceiling_is_continuous(self):
        pri = CFG.levels[0]
        prev = None
        for t10 in range(40, 120):
            c = danger_ceiling(pri, t10 / 10, CFG.rules)
            if prev is not None:
                self.assertLess(abs(c - prev), 0.01)
            prev = c

    def test_boards(self):
        self.assertEqual(board_for("principiante", 1.0, 9, CFG), "softboard")
        self.assertEqual(board_for("intermedio", 0.6, 6, CFG), "longboard")
        self.assertEqual(board_for("intermedio", 0.8, 5, CFG), "funboard-mini")
        self.assertEqual(board_for("intermedio", 1.2, 7, CFG), "shortboard")
        self.assertEqual(board_for("avanzado", 2.0, 9, CFG), "shortboard step-up")


class TestEncargoPrincipiante(unittest.TestCase):
    """Los casos de la sección 7 del encargo, en Barceloneta y para principiante.

    Donde el resultado difiere del encargo es por los cambios aprobados en el plan
    (techo 1,1 m, curva de periodo sobre pico); el comentario lo indica.
    """

    def check(self, res, verdict, score=None, places=1):
        p = res["principiante"]
        self.assertEqual(p.verdict, verdict, p)
        if score is not None:
            self.assertAlmostEqual(p.display, score, places=places)
        return p

    def test_01_flat(self):
        self.check(hour(0.25, 6, TERRAL, 5), FLAT)

    def test_02_onshore_short_period(self):
        self.check(hour(0.5, 4, MAR, 18), RED)

    def test_03_small_clean(self):
        # Encargo: 🟢 ≈ 6,9 dudoso. Con la curva de periodo sobre pico: 8,5.
        self.check(hour(0.5, 6.5, TERRAL, 8), GREEN, 8.5)

    def test_04_good_light_onshore(self):
        self.check(hour(0.8, 7, MAR, 6), GREEN, 8.75)

    def test_05_onshore_choppy(self):
        p = self.check(hour(0.8, 7, MAR, 25), RED)
        self.assertIn("revuelto", p.reason)

    def test_06_one_meter_eight_seconds(self):
        # Encargo: 🟢 10. Con el techo de 1,1 m (0,98 m a 8 s) es demasiado para principiante.
        res = hour(1.0, 8, TERRAL, 12)
        self.check(res, DANGER)
        self.assertEqual(res["intermedio"].verdict, GREEN)

    def test_07_storm(self):
        self.check(hour(2.0, 9, E, 35), DANGER)

    def test_08_cross_light(self):
        # Encargo: 🟡 ≈ 5,5. Con la curva de periodo sobre pico: 🟢 8,5.
        self.check(hour(0.7, 5.5, NE, 8), GREEN, 8.5)

    def test_09_big_messy(self):
        # Encargo: 🔴 ≈ 3,8. Con el techo de 1,1 m: ⛔.
        self.check(hour(1.3, 6, MAR, 5), DANGER)

    def test_10_afternoon_sea_breeze(self):
        self.check(hour(0.6, 5, MAR, 12), RED, 3.75)

    def test_11_strong_offshore(self):
        # Encargo: 🟡 ≈ 6. Con la curva de periodo sobre pico: 🟢 6,75 dudoso.
        p = self.check(hour(0.8, 7, TERRAL, 30), GREEN, 6.75)
        self.assertTrue(p.doubtful)

    def test_12_big_long_period(self):
        self.check(hour(1.3, 8.5, TERRAL, 5), DANGER)

    def test_13_shadow_by_direction(self):
        b = hour(0.9, 7, 0, 3, wave_dir=65)["principiante"]
        c = hour(0.9, 7, 0, 3, wave_dir=65, spot=CFG.spot("castelldefels"))["principiante"]
        self.assertAlmostEqual(b.heff, 0.51, places=2)
        self.assertAlmostEqual(c.heff, 0.33, places=2)
        self.assertEqual(b.verdict, GREEN)
        self.assertEqual(c.verdict, RED)


class TestLevels(unittest.TestCase):
    """Casos que discriminan los tres niveles (Barceloneta, ola de frente)."""

    CASES = [
        # (Heff, T, dir viento, km/h) → (principiante, intermedio, avanzado)
        ((0.60, 6.0, TERRAL, 10), (GREEN, YELLOW, YELLOW)),
        ((2.00, 9.0, TERRAL, 12), (DANGER, DANGER, GREEN)),
        ((0.45, 4.4, TERRAL, 8), (YELLOW, YELLOW, YELLOW)),
        ((0.80, 5.0, TERRAL, 10), (GREEN, GREEN, YELLOW)),
        ((1.20, 7.0, TERRAL, 12), (DANGER, GREEN, GREEN)),
        ((0.90, 5.5, MAR, 20), (RED, RED, RED)),
        ((1.60, 8.0, TERRAL, 25), (DANGER, GREEN, GREEN)),   # intermedio al filo: techo 1,60
        ((2.60, 10.0, TERRAL, 40), (DANGER, DANGER, DANGER)),
        ((0.28, 5.0, TERRAL, 8), (FLAT, FLAT, YELLOW)),
        ((0.60, 6.0, MAR, 25), (RED, RED, RED)),              # avanzado sin veto, pero 1,0
    ]

    def test_table(self):
        for args, expected in self.CASES:
            res = hour(*args)
            got = tuple(res[lv].verdict for lv in LEVELS)
            self.assertEqual(got, expected, f"{args}: {got}")

    def test_small_day_is_longboard_not_red_for_advanced(self):
        a = hour(0.6, 6, TERRAL, 10)["avanzado"]
        self.assertEqual(a.verdict, YELLOW)
        self.assertEqual(a.display, CFG.rules["small_day_cap"])
        self.assertEqual(board_for("avanzado", a.heff, a.period, CFG), "longboard")
        self.assertEqual(a.notify, 0.0)   # un 🟡 de cortesía no hace sonar el aviso

    def test_boards_by_case(self):
        res = hour(0.8, 5.0, TERRAL, 10)
        self.assertEqual(board_for("intermedio", res["intermedio"].heff, 5.0, CFG), "funboard-mini")
        self.assertEqual(board_for("avanzado", res["avanzado"].heff, 5.0, CFG), "longboard")


class TestCumulativeInvariants(unittest.TestCase):
    def test_thresholds_are_monotonic_per_spot(self):
        for spot in CFG.spots:
            lv = [CFG.level_at(l, spot) for l in CFG.levels]
            for lo, hi in zip(lv, lv[1:]):
                with self.subTest(spot=spot.id, lo=lo.id, hi=hi.id):
                    self.assertLessEqual(lo.heff_danger, hi.heff_danger)
                    self.assertLessEqual(lo.wind_max, hi.wind_max)
                    self.assertLessEqual(lo.gust_max, hi.gust_max)
                    self.assertLessEqual(lo.onshore_veto, hi.onshore_veto)
                    self.assertGreaterEqual(lo.flat_threshold, hi.flat_threshold)
                    self.assertGreaterEqual(lo.t_peak_min, hi.t_peak_min)

    def test_sweep(self):
        """Para cualquier condición: si un nivel no es ⛔ el superior tampoco,
        y si un nivel tiene 🟢/🟡 el superior no queda en 🔴/⚪."""
        grid = itertools.product(
            [0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0],
            [3.5, 4.0, 4.5, 5.5, 7.0, 8.5, 10.0],
            [0, 45, 90, 135, 180, 270, 315],
            [3, 10, 20, 27, 37, 50],
        )
        for h, t, wd, ws in grid:
            for spot in CFG.spots:
                res = hour(h, t, wd, ws, spot=spot)
                for lo, hi in zip(LEVELS, LEVELS[1:]):
                    a, b = res[lo], res[hi]
                    if a.verdict != DANGER:
                        self.assertNotEqual(b.verdict, DANGER, (spot.id, h, t, wd, ws, lo))
                    if a.verdict in (GREEN, YELLOW):
                        self.assertIn(b.verdict, (GREEN, YELLOW), (spot.id, h, t, wd, ws, lo))


# --- Veredicto del día ---------------------------------------------------------

DAY = date(2026, 10, 23)


def forecast(spot, rows, day=DAY):
    """rows: [(hora, H, T, dir viento, km/h, dir ola)]."""
    hours = [Hour(time=datetime(day.year, day.month, day.day, hh), wave_height=h,
                  wave_direction=wdir, wave_period=t, peak_period=t, swell_height=0.0, sst=20.0,
                  wind_speed=ws, wind_direction=wd, gusts=ws, precipitation=0.0)
             for hh, h, t, wd, ws, wdir in rows]
    sun = {day: (datetime(day.year, day.month, day.day, 8, 10),
                 datetime(day.year, day.month, day.day, 18, 55))}
    return SpotForecast(spot.id, (0, 0), hours, sun)


def level_series(sf, spot, level_id):
    idx = LEVELS.index(level_id)
    return [(h, score_hour(Conditions.from_hour(h), spot, CFG)[idx]) for h in sf.hours]


class TestDay(unittest.TestCase):
    GOOD = (0.8, 7, TERRAL, 8, 135)
    BAD = (0.8, 7, MAR, 25, 135)

    def day(self, pattern, level="principiante"):
        rows = [(9 + i, *(self.GOOD if c == "g" else self.BAD)) for i, c in enumerate(pattern)]
        sf = forecast(BCN, rows)
        return day_verdict(level_series(sf, BCN, level), BCN.id, level, DAY, CFG)

    def test_single_good_hour_is_only_yellow(self):
        dv = self.day("bbgbb")
        self.assertEqual(dv.verdict, YELLOW)
        self.assertTrue(dv.single_hour)
        self.assertEqual(dv.window, (11, 12))
        self.assertFalse(dv.notify)

    def test_two_good_hours_are_green_and_notify(self):
        dv = self.day("bggbb")
        self.assertEqual(dv.verdict, GREEN)
        self.assertEqual(dv.window, (10, 12))
        self.assertTrue(dv.notify)

    def test_longest_window_wins(self):
        dv = self.day("ggbggg")
        self.assertEqual(dv.window, (12, 15))

    def test_all_bad(self):
        dv = self.day("bbbb")
        self.assertEqual(dv.verdict, RED)
        self.assertIn("revuelto", dv.reason)
        self.assertIsNone(dv.window)

    def test_daylight_only(self):
        rows = [(6, *self.GOOD), (7, *self.GOOD), (19, *self.GOOD), (20, *self.GOOD)]
        data = {s.id: forecast(s, rows) for s in CFG.spots}
        s = summarize_day(data, CFG, DAY, datetime(2026, 10, 23, 7))
        self.assertIsNone(s.headline.window)
        self.assertEqual(s.headline.reason, "sin datos")

    def test_small_day_does_not_notify_for_advanced(self):
        rows = [(h, 0.3, 6, TERRAL, 5, 135) for h in range(9, 18)]
        data = {s.id: forecast(s, rows) for s in CFG.spots}
        s = summarize_day(data, CFG, DAY, datetime(2026, 10, 23, 7))
        self.assertEqual(s.best["avanzado"].verdict, YELLOW)
        self.assertFalse(s.notify)


class TestGarraf(unittest.TestCase):
    def test_never_recommended_to_beginners(self):
        # Ola de 165° (de frente para Garraf) en todos los spots: Garraf puntúa mejor.
        rows = [(h, 0.75, 7, 345, 6, 165) for h in range(9, 15)]
        data = {s.id: forecast(s, rows) for s in CFG.spots}
        s = summarize_day(data, CFG, DAY, datetime(2026, 10, 23, 7))
        g = s.verdicts["garraf"]["principiante"]
        best_score = max(v["principiante"].score for v in s.verdicts.values())
        self.assertEqual(g.score, best_score)              # Garraf es el mejor sobre el papel…
        self.assertNotEqual(s.best["principiante"].spot_id, "garraf")   # …pero no se recomienda
        self.assertNotEqual(s.headline.spot_id, "garraf")


class TestBadDayHeadline(unittest.TestCase):
    def test_storm_is_danger_for_beginners_not_just_red(self):
        # Viento del SSE: de mar en todos los spots. Avanzado 🔴 (revuelto), principiante ⛔.
        rows = [(h, 2.0, 9, 150, 35, 135) for h in range(9, 15)]
        data = {s.id: forecast(s, rows) for s in CFG.spots}
        s = summarize_day(data, CFG, DAY, datetime(2026, 10, 23, 7))
        self.assertFalse(s.good)
        self.assertEqual(s.headline.verdict, DANGER)


if __name__ == "__main__":
    unittest.main()
