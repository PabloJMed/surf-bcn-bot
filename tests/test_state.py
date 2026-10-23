import csv
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from surfbot.fetch import FetchError, get_json
from surfbot.state import save_state, load_state, should_send, write_history
from surfbot.telegram import TelegramError, clean_token, send_message, token_problem

FAKE_TOKEN = "1234567890:" + "A" * 35   # con forma de token real, sin literal en el repo


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class TestShouldSend(unittest.TestCase):
    """Crons a las 4:47 y 5:47 UTC. Hoy 23/10 es horario de verano (UTC+2)."""

    def test_too_early(self):
        # 25/10 ya es invierno (UTC+1): 4:47 UTC = 5:47 en Madrid
        self.assertFalse(should_send(utc(2026, 10, 25, 4, 47), None)[0])

    def test_sends_at_0647(self):
        self.assertTrue(should_send(utc(2026, 10, 25, 5, 47), None)[0])      # invierno
        self.assertTrue(should_send(utc(2026, 10, 23, 4, 47), None)[0])      # verano

    def test_second_run_same_day(self):
        self.assertFalse(should_send(utc(2026, 10, 23, 5, 47), "2026-10-23")[0])

    def test_force(self):
        self.assertTrue(should_send(utc(2026, 10, 23, 5, 47), "2026-10-23", force=True)[0])
        self.assertTrue(should_send(utc(2026, 10, 25, 4, 47), None, force=True)[0])

    def test_delayed_cron_still_sends(self):
        self.assertTrue(should_send(utc(2026, 10, 23, 6, 30), "2026-10-22")[0])


class TestFiles(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            self.assertEqual(load_state(p), {})
            save_state(p, {"last_sent": "2026-10-23"})
            self.assertEqual(load_state(p)["last_sent"], "2026-10-23")

    def test_history_replaces_same_run_date(self):
        row = lambda rd, spot: {"run_date": rd, "day": rd, "spot": spot, "level": "principiante"}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "history.csv"
            write_history(p, [row("2026-10-22", "a"), row("2026-10-22", "b")])
            write_history(p, [row("2026-10-23", "a")])
            write_history(p, [row("2026-10-23", "b")])       # reenvío forzado
            with p.open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([(r["run_date"], r["spot"]) for r in rows],
                             [("2026-10-22", "a"), ("2026-10-22", "b"), ("2026-10-23", "b")])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestTelegram(unittest.TestCase):
    def test_error_never_leaks_token(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b'{"ok":false,"description":"Unauthorized"}'))
        with self.assertRaises(TelegramError) as cm:
            send_message(FAKE_TOKEN, "1", "hola", opener=opener)
        msg = str(cm.exception)
        self.assertIn("401", msg)
        self.assertIn("Unauthorized", msg)
        self.assertNotIn(FAKE_TOKEN, msg)
        self.assertNotIn("api.telegram.org", msg)
        self.assertIsNone(cm.exception.__cause__)

    def test_network_error_never_leaks_token(self):
        def opener(req, timeout):
            raise urllib.error.URLError("timed out")
        with self.assertRaises(TelegramError) as cm:
            send_message(FAKE_TOKEN, "1", "hola", opener=opener)
        self.assertNotIn(FAKE_TOKEN, str(cm.exception))

    def test_clean_token(self):
        self.assertEqual(clean_token(f"  {FAKE_TOKEN}\n"), FAKE_TOKEN)
        self.assertEqual(clean_token(f"\x1b[200~{FAKE_TOKEN}\x1b[201~"), FAKE_TOKEN)
        self.assertEqual(clean_token(f'"{FAKE_TOKEN}"'), FAKE_TOKEN)

    def test_token_problem_never_reveals_token(self):
        self.assertIsNone(token_problem(FAKE_TOKEN))
        for bad in ("", "hola", FAKE_TOKEN[:20], FAKE_TOKEN.replace(":", " : ")):
            msg = token_problem(bad)
            self.assertIsNotNone(msg, bad)
            if bad:
                self.assertNotIn(bad, msg)

    def test_silent_flag(self):
        sent = {}

        def opener(req, timeout):
            sent.update(json.loads(req.data))
            return FakeResponse(b'{"ok":true,"result":{}}')
        send_message(FAKE_TOKEN, "42", "<b>hola</b>", silent=True, opener=opener)
        self.assertEqual(sent["parse_mode"], "HTML")
        self.assertTrue(sent["disable_notification"])


class TestFetchRetries(unittest.TestCase):
    def test_three_attempts_with_backoff(self):
        calls, sleeps = [], []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.URLError("down")
        with self.assertRaises(FetchError):
            get_json("https://example.invalid", {}, attempts=3, backoff=1,
                     sleep=sleeps.append, opener=opener)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_recovers_on_retry(self):
        n = []

        def opener(req, timeout):
            n.append(1)
            if len(n) < 2:
                raise urllib.error.URLError("down")
            return FakeResponse(b'{"ok": 1}')
        self.assertEqual(get_json("https://x", {}, sleep=lambda s: None, opener=opener), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
