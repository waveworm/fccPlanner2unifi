import unittest

from py_app.settings import Settings
from py_app.vendors.unifi_access import UnifiAccessClient


class BuildWeekScheduleTests(unittest.TestCase):
    def test_splits_overnight_windows(self) -> None:
        client = UnifiAccessClient(
            Settings(
                UNIFI_ACCESS_BASE_URL="https://127.0.0.1",
                DISPLAY_TIMEZONE="America/New_York",
            )
        )

        weekly = client._build_week_schedule(
            [
                {
                    "openStart": "2026-03-08T21:30:00Z",
                    "openEnd": "2026-03-09T11:30:00Z",
                }
            ]
        )

        self.assertEqual(weekly["sunday"], [{"start_time": "17:30:00", "end_time": "23:59:59"}])
        self.assertEqual(weekly["monday"], [{"start_time": "00:00:00", "end_time": "07:30:00"}])


if __name__ == "__main__":
    unittest.main()
