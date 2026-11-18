import unittest

import pandas as pd

from analyze import decide, parse_timestamps, project_to_route


class AnalysisTests(unittest.TestCase):
    def test_invalid_device_timestamp_is_rejected_without_losing_valid_rows(self):
        parsed = parse_timestamps(
            pd.Series(
                [
                    "2026-06-16 06:60:23+05:30",
                    "2026-06-16 06:42:24+05:30",
                ]
            )
        )
        self.assertTrue(pd.isna(parsed.iloc[0]))
        self.assertFalse(pd.isna(parsed.iloc[1]))

    def test_route_projection_returns_fraction_and_cross_track_distance(self):
        stops = pd.DataFrame(
            {
                "seq": [1, 2],
                "lat": [0.0, 0.0],
                "lon": [0.0, 1.0],
            }
        )
        cross_track, progress = project_to_route(0.0, 0.5, stops)
        self.assertAlmostEqual(cross_track, 0.0, places=6)
        self.assertAlmostEqual(progress, 0.5, places=6)

    def test_reporting_policy_overrides_a_measured_delay(self):
        self.assertEqual(decide(12, 0.1, 9.2, False, False), "HOLD")

    def test_slow_fresh_trip_is_a_driver_call_not_a_push(self):
        self.assertEqual(decide(11, 0.1, 59.0, True, True), "CALL_DRIVER")

    def test_stale_slow_tail_is_a_driver_call_but_other_stale_data_is_no_verdict(self):
        self.assertEqual(decide(17, 17.3, 9.4, False, True), "CALL_DRIVER")
        self.assertEqual(decide(14, 48.0, 48.5, False, False), "NO_VERDICT")

    def test_only_fresh_material_lag_can_be_pushed(self):
        self.assertEqual(decide(9, 0.2, 5.9, False, False), "PUSH_LATE 5 min")
        self.assertEqual(decide(21, 0.1, -0.5, False, False), "HOLD")


if __name__ == "__main__":
    unittest.main()
