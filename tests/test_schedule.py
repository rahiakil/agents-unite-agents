import unittest
from datetime import date, datetime, timezone

import support  # noqa: F401

from harness.personas import load_personas
from harness.schedule import cron_times_utc, due_slots, jitter_minutes

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


if __name__ == "__main__":
    unittest.main()
