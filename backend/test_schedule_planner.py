"""Schedule arithmetic tests. Pure, no clock of their own, no database."""

import unittest
from datetime import datetime

from services.run_metrics import percentile, portfolio, summarise, trend
from services.schedule_planner import (
    CRON_FOR_FREQUENCY,
    ScheduleError,
    advance,
    describe,
    next_cron_run,
    parse_cron,
    resolve,
)

NOW = datetime(2026, 10, 9, 13, 42, 17)  # a Friday


class CronParsingTests(unittest.TestCase):
    def test_wildcard_expands_to_every_value(self):
        minutes, hours, days, months, weekdays = parse_cron("* * * * *")
        self.assertEqual(len(minutes), 60)
        self.assertEqual(len(hours), 24)
        self.assertEqual(len(days), 31)
        self.assertEqual(len(months), 12)
        self.assertEqual(len(weekdays), 7)

    def test_lists_ranges_and_steps(self):
        self.assertEqual(parse_cron("0,30 * * * *")[0], [0, 30])
        self.assertEqual(parse_cron("0 9-17 * * *")[1], list(range(9, 18)))
        self.assertEqual(parse_cron("*/15 * * * *")[0], [0, 15, 30, 45])
        # Standard cron: 1-5 is Monday to Friday.
        self.assertEqual(parse_cron("0 0 * * 1-5")[4], [1, 2, 3, 4, 5])

    def test_wrong_field_count_is_rejected(self):
        for expression in ("* * * *", "* * * * * *", ""):
            with self.subTest(expression=expression):
                with self.assertRaises(ScheduleError):
                    parse_cron(expression)

    def test_out_of_range_values_are_rejected_by_field_name(self):
        with self.assertRaises(ScheduleError) as ctx:
            parse_cron("60 * * * *")
        self.assertIn("minute", str(ctx.exception))

        with self.assertRaises(ScheduleError) as ctx:
            parse_cron("0 24 * * *")
        self.assertIn("hour", str(ctx.exception))

        with self.assertRaises(ScheduleError) as ctx:
            parse_cron("0 0 0 * *")
        self.assertIn("day-of-month", str(ctx.exception))

    def test_non_numeric_and_bad_step_are_rejected(self):
        for expression in ("abc * * * *", "*/0 * * * *", "5-1 * * * *"):
            with self.subTest(expression=expression):
                with self.assertRaises(ScheduleError):
                    parse_cron(expression)


class NextRunTests(unittest.TestCase):
    def test_next_run_is_strictly_after_now(self):
        self.assertEqual(
            next_cron_run("* * * * *", NOW), datetime(2026, 10, 9, 13, 43)
        )

    def test_hourly_lands_on_the_next_hour(self):
        self.assertEqual(
            next_cron_run("0 * * * *", NOW), datetime(2026, 10, 9, 14, 0)
        )

    def test_daily_at_two_rolls_to_tomorrow_when_today_has_passed(self):
        self.assertEqual(
            next_cron_run("0 2 * * *", NOW), datetime(2026, 10, 10, 2, 0)
        )

    def test_weekly_monday_from_a_friday(self):
        result = next_cron_run("0 2 * * 1", NOW)
        self.assertEqual(result, datetime(2026, 10, 12, 2, 0))
        self.assertEqual(result.weekday(), 0)

    def test_monthly_rolls_to_the_first_of_next_month(self):
        self.assertEqual(
            next_cron_run("0 2 1 * *", NOW), datetime(2026, 11, 1, 2, 0)
        )

    def test_a_schedule_that_never_fires_returns_none(self):
        # 31 February does not exist.
        self.assertIsNone(next_cron_run("0 0 31 2 *", NOW))

    def test_day_of_month_or_day_of_week_matches_when_both_are_restricted(self):
        # The 15th OR a Monday - standard cron semantics.
        result = next_cron_run("0 0 15 * 1", datetime(2026, 10, 9, 0, 0))
        self.assertEqual(result, datetime(2026, 10, 12, 0, 0))  # Monday first


class ResolveTests(unittest.TestCase):
    def test_named_frequencies_map_to_their_cron(self):
        for frequency, cron in CRON_FOR_FREQUENCY.items():
            with self.subTest(frequency=frequency):
                plan = resolve(frequency, NOW)
                self.assertEqual(plan["cron_expression"], cron)
                self.assertGreater(plan["next_run_at"], NOW)
                self.assertIsNone(plan["run_at"])

    def test_once_requires_a_future_run_at(self):
        with self.assertRaises(ScheduleError):
            resolve("once", NOW)
        with self.assertRaises(ScheduleError):
            resolve("once", NOW, run_at=datetime(2020, 1, 1))

        future = datetime(2026, 12, 25, 9, 0)
        plan = resolve("once", NOW, run_at=future)
        self.assertEqual(plan["next_run_at"], future)
        self.assertIsNone(plan["cron_expression"])

    def test_cron_requires_an_expression(self):
        with self.assertRaises(ScheduleError):
            resolve("cron", NOW)
        plan = resolve("cron", NOW, cron_expression="*/10 * * * *")
        self.assertEqual(plan["cron_expression"], "*/10 * * * *")

    def test_unknown_frequency_names_the_valid_options(self):
        with self.assertRaises(ScheduleError) as ctx:
            resolve("fortnightly", NOW)
        self.assertIn("hourly", str(ctx.exception))

    def test_frequency_is_case_insensitive(self):
        self.assertEqual(resolve("DAILY", NOW)["frequency"], "daily")


class AdvanceAndDescribeTests(unittest.TestCase):
    def test_a_one_off_schedule_has_no_next_run(self):
        self.assertIsNone(advance("once", None, NOW))

    def test_advance_uses_the_named_frequency_when_cron_is_absent(self):
        self.assertEqual(advance("hourly", None, NOW), datetime(2026, 10, 9, 14, 0))

    def test_describe_is_readable_for_every_frequency(self):
        self.assertEqual(describe("hourly", None), "Every hour, on the hour")
        self.assertEqual(describe("weekly", None), "Every Monday at 02:00")
        self.assertIn("Custom cron", describe("cron", "*/5 * * * *"))
        self.assertIn("2026", describe("once", None, datetime(2026, 12, 1, 8, 0)))


class RunMetricsTests(unittest.TestCase):
    RUNS = [
        {"id": "4", "status": "running", "execution_time": None, "row_count": None},
        {"id": "3", "status": "completed", "execution_time": 60.0, "row_count": 100},
        {"id": "2", "status": "failed", "execution_time": 12.0, "row_count": None},
        {"id": "1", "status": "completed", "execution_time": 40.0, "row_count": 200},
    ]

    def test_percentile_handles_empty_single_and_many(self):
        self.assertIsNone(percentile([], 0.95))
        self.assertEqual(percentile([7.0], 0.95), 7.0)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.0), 1.0)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 1.0), 4.0)

    def test_summarise_counts_states_and_rows(self):
        metrics = summarise(self.RUNS)
        self.assertEqual(metrics["total_runs"], 4)
        self.assertEqual(metrics["succeeded"], 2)
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["running"], 1)
        self.assertEqual(metrics["total_rows_processed"], 300)
        self.assertEqual(metrics["last_status"], "running")

    def test_success_rate_excludes_in_flight_runs(self):
        self.assertAlmostEqual(summarise(self.RUNS)["success_rate"], 66.67, places=1)
        self.assertIsNone(summarise([{"status": "running"}])["success_rate"])

    def test_summarise_of_no_runs_is_all_empty_not_an_error(self):
        metrics = summarise([])
        self.assertEqual(metrics["total_runs"], 0)
        self.assertIsNone(metrics["success_rate"])
        self.assertIsNone(metrics["avg_duration_seconds"])
        self.assertIsNone(metrics["last_run"])
        self.assertEqual(metrics["total_rows_processed"], 0)

    def test_trend_is_oldest_first_for_a_left_to_right_sparkline(self):
        points = trend(self.RUNS)
        self.assertEqual([point["run_id"] for point in points], ["1", "2", "3", "4"])
        self.assertTrue(points[0]["succeeded"])
        self.assertFalse(points[1]["succeeded"])

    def test_trend_respects_the_window(self):
        self.assertEqual(len(trend(self.RUNS, limit=2)), 2)

    def test_portfolio_rolls_up_and_counts_problem_pipelines(self):
        totals = portfolio(
            [
                {"pipeline_id": "a", "metrics": summarise(self.RUNS)},
                {"pipeline_id": "b", "metrics": summarise([])},
                {
                    "pipeline_id": "c",
                    "metrics": summarise([{"status": "failed", "row_count": None}]),
                },
            ]
        )
        self.assertEqual(totals["pipelines"], 3)
        self.assertEqual(totals["total_runs"], 5)
        self.assertEqual(totals["pipelines_never_run"], 1)
        self.assertEqual(totals["pipelines_failing"], 1)
        self.assertEqual(totals["total_rows_processed"], 300)


if __name__ == "__main__":
    unittest.main()
