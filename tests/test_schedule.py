import unittest
from datetime import date, datetime, timezone

import support  # noqa: F401

from harness.personas import load_personas
from harness.schedule import (
    cron_times_utc,
    is_market_holiday,
    is_trading_day,
    market_holidays,
    due_slots,
    jitter_minutes,
    ledger_accepts_report_date,
    passed_slots,
    previous_trading_day,
    slot_target,
)

ET_DATES = (date(2026, 11, 23), date(2026, 7, 13))  # Monday EST and Monday EDT


class ScheduleTests(unittest.TestCase):
    def test_jitter_is_stable_and_bounded(self):
        day = date(2026, 11, 23)
        first = jitter_minutes("au-mira-retail", day, "premarket", 10)
        second = jitter_minutes("au-mira-retail", day, "premarket", 10)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, -10)
        self.assertLessEqual(first, 10)
        other = jitter_minutes("au-mira-retail", date(2026, 11, 24), "premarket", 10)
        self.assertIsInstance(other, int)

    def test_every_slot_is_due_on_a_winter_and_summer_monday(self):
        personas = load_personas()
        for day in ET_DATES:
            seen = set()
            for moment in cron_times_utc(day):
                for persona in personas:
                    for slot, _target in due_slots(persona, moment, session=day):
                        seen.add((persona.id, slot.id))
            expected = {(persona.id, slot.id) for persona in personas for slot in persona.slots}
            self.assertEqual(seen, expected, msg=f"missed slots on {day}: {expected - seen}")

    def test_one_wake_is_not_everyone(self):
        personas = load_personas()
        moment = datetime(2026, 11, 23, 15, 5, tzinfo=timezone.utc)
        due = []
        for persona in personas:
            due.extend(due_slots(persona, moment, session=date(2026, 11, 23)))
        self.assertGreater(len(due), 0)
        self.assertLess(len(due), len(personas))

    def test_weekend_is_empty(self):
        personas = load_personas()
        moment = datetime(2026, 11, 21, 15, 5, tzinfo=timezone.utc)
        for persona in personas:
            self.assertEqual(due_slots(persona, moment), [])
            self.assertEqual(passed_slots(persona, moment, date(2026, 11, 21)), [])

    def test_passed_slots_cover_elapsed_slots_and_skip_the_future(self):
        personas = load_personas()
        day = date(2026, 11, 23)
        # 13:30 ET. Morning slots have passed. The close has not.
        moment = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)
        elapsed = []
        waiting = []
        expected_elapsed = []
        for persona in personas:
            got = passed_slots(persona, moment, day)
            for slot, target in got:
                elapsed.append((persona.id, slot.id))
                self.assertLessEqual(target, moment)
            for slot in persona.slots:
                target = slot_target(persona.id, day, slot)
                if target <= moment:
                    expected_elapsed.append((persona.id, slot.id))
                else:
                    waiting.append((persona.id, slot.id))
        self.assertEqual(sorted(elapsed), sorted(expected_elapsed))
        self.assertGreaterEqual(len(elapsed), 8)
        self.assertGreater(len(waiting), 0)
        self.assertTrue(set(waiting).isdisjoint(elapsed))
        window = []
        for persona in personas:
            window.extend(due_slots(persona, moment, session=day))
        self.assertLess(len(window), len(elapsed))

    def test_previous_trading_day_and_ledger_date_bound(self):
        monday = date(2026, 11, 23)
        friday = date(2026, 11, 20)
        thursday = date(2026, 11, 19)
        self.assertEqual(previous_trading_day(monday), friday)
        self.assertEqual(previous_trading_day(date(2026, 11, 22)), friday)
        self.assertTrue(ledger_accepts_report_date(monday, today=monday))
        self.assertTrue(ledger_accepts_report_date(friday, today=monday))
        self.assertFalse(ledger_accepts_report_date(thursday, today=monday))
        self.assertFalse(ledger_accepts_report_date(date(2026, 11, 21), today=monday))
        self.assertFalse(ledger_accepts_report_date(date(2026, 11, 24), today=monday))
        self.assertTrue(ledger_accepts_report_date(friday, today=date(2026, 11, 22)))
        self.assertFalse(ledger_accepts_report_date(thursday, today=date(2026, 11, 22)))


if __name__ == "__main__":
    unittest.main()

    def test_nyse_holidays(self):
        expected_2026 = {
            date(2026, 1, 1),
            date(2026, 1, 19),
            date(2026, 2, 16),
            date(2026, 4, 3),
            date(2026, 5, 25),
            date(2026, 6, 19),
            date(2026, 7, 3),
            date(2026, 9, 7),
            date(2026, 11, 26),
            date(2026, 12, 25),
        }
        self.assertEqual(market_holidays(2026), expected_2026)
        self.assertIn(date(2027, 3, 26), market_holidays(2027))  # Good Friday 2027
        self.assertIn(date(2027, 12, 24), market_holidays(2027))  # Christmas on Saturday
        self.assertNotIn(date(2021, 12, 31), market_holidays(2021))  # New Year on Saturday
        self.assertTrue(is_market_holiday(date(2026, 11, 26)))
        self.assertFalse(is_trading_day(date(2026, 11, 26)))
        self.assertTrue(is_trading_day(date(2026, 11, 27)))  # early close is still a session
        self.assertFalse(is_trading_day(date(2026, 11, 28)))

    def test_holiday_has_no_slots_and_is_skipped_by_catch_up(self):
        thanksgiving = date(2026, 11, 26)
        friday = date(2026, 11, 27)
        self.assertEqual(previous_trading_day(friday), date(2026, 11, 25))
        self.assertEqual(previous_trading_day(date(2026, 11, 30)), friday)
        self.assertFalse(ledger_accepts_report_date(thanksgiving, today=thanksgiving))
        self.assertFalse(ledger_accepts_report_date(thanksgiving, today=friday))
        self.assertTrue(ledger_accepts_report_date(date(2026, 11, 25), today=friday))
        late = datetime(2026, 11, 27, 3, 0, tzinfo=timezone.utc)
        for persona in load_personas():
            self.assertEqual(passed_slots(persona, late, thanksgiving), [])
            self.assertEqual(due_slots(persona, late, session=thanksgiving, lookback_minutes=24 * 60), [])
