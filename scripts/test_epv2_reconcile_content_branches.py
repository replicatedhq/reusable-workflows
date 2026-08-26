#!/usr/bin/env python3
"""Unit tests for the pure decision logic in epv2_reconcile_content_branches.

Covers the pure decision logic:
  * bare-version format (EP v2's exact-match rule),
  * version selection / bounding / pre-release filtering,
  * idempotency (never re-create or force-update an existing branch),
  * label-scheme mismatch detection (channel had releases, zero bare matches),
  * run-summary count computation and rendering,
  * loud env parsing for the release limit.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""
import os
import unittest

import epv2_reconcile_content_branches as rc


class IsReleaseVersionTests(unittest.TestCase):
    def test_accepts_bare_version(self):
        self.assertTrue(rc.is_release_version("0.3.312"))
        self.assertTrue(rc.is_release_version("10.20.300"))

    def test_rejects_pr_prerelease(self):
        # PR releases look like "0.3.153-pr.106" -- must NOT get a branch.
        self.assertFalse(rc.is_release_version("0.3.153-pr.106"))

    def test_rejects_v_prefix(self):
        self.assertFalse(rc.is_release_version("v0.3.312"))

    def test_rejects_release_slash_prefix(self):
        self.assertFalse(rc.is_release_version("release/v0.3.312"))

    def test_rejects_build_metadata(self):
        self.assertFalse(rc.is_release_version("0.3.312+build.7"))

    def test_rejects_partial_and_empty(self):
        self.assertFalse(rc.is_release_version("0.3"))
        self.assertFalse(rc.is_release_version(""))
        self.assertFalse(rc.is_release_version("   "))

    def test_tolerates_surrounding_whitespace(self):
        self.assertTrue(rc.is_release_version("  0.3.312  "))


class BranchNameTests(unittest.TestCase):
    def test_branch_name_is_bare_version(self):
        # The whole point: no "v", no "release/" prefix -- just the bare version.
        self.assertEqual(rc.branch_name("0.3.312"), "0.3.312")

    def test_branch_name_strips_whitespace(self):
        self.assertEqual(rc.branch_name("  0.3.312 "), "0.3.312")

    def test_branch_name_rejects_non_bare(self):
        with self.assertRaises(ValueError):
            rc.branch_name("v0.3.312")
        with self.assertRaises(ValueError):
            rc.branch_name("0.3.312-pr.1")


class SelectVersionsTests(unittest.TestCase):
    def _rel(self, *semvers):
        return [{"semver": s} for s in semvers]

    def test_dedupes_across_channels(self):
        by_channel = {
            "Stable": self._rel("0.3.166"),
            "Unstable": self._rel("0.3.310", "0.3.166"),
        }
        selected, _ = rc.select_versions(by_channel, limit=20)
        self.assertEqual(selected, ["0.3.166", "0.3.310"])

    def test_filters_prereleases_and_logs_them(self):
        by_channel = {"PRPreview": self._rel("0.3.153-pr.106", "0.3.200")}
        selected, skipped = rc.select_versions(by_channel, limit=20)
        self.assertEqual(selected, ["0.3.200"])
        self.assertTrue(any("0.3.153-pr.106" in line for line in skipped))

    def test_bounds_to_limit_and_logs_truncation(self):
        by_channel = {"Unstable": self._rel("0.3.310", "0.3.308", "0.3.306")}
        selected, skipped = rc.select_versions(by_channel, limit=2)
        self.assertEqual(selected, ["0.3.308", "0.3.310"])
        # No silent truncation: the dropped release is logged.
        self.assertTrue(any("0.3.306" in line and "beyond latest 2" in line for line in skipped))

    def test_empty_channel_is_safe(self):
        selected, skipped = rc.select_versions({"Beta": []}, limit=20)
        self.assertEqual(selected, [])
        self.assertEqual(skipped, [])

    def test_empty_semver_is_skipped_and_logged(self):
        selected, skipped = rc.select_versions({"Unstable": [{"semver": ""}]}, limit=20)
        self.assertEqual(selected, [])
        self.assertTrue(any("empty semver" in line for line in skipped))

    def test_numeric_sort_not_lexical(self):
        by_channel = {"Unstable": self._rel("0.3.9", "0.3.10", "0.3.100")}
        selected, _ = rc.select_versions(by_channel, limit=20)
        self.assertEqual(selected, ["0.3.9", "0.3.10", "0.3.100"])


class ChannelZeroMatchWarningsTests(unittest.TestCase):
    def _rel(self, *semvers):
        return [{"semver": s} for s in semvers]

    def test_flags_channel_with_releases_but_no_bare_matches(self):
        # A v-prefixed label scheme: releases exist, but none are bare versions.
        by_channel = {"Stable": self._rel("v0.3.312", "v0.3.311")}
        self.assertEqual(rc.channel_zero_match_warnings(by_channel), ["Stable"])

    def test_no_flag_when_at_least_one_bare_match(self):
        by_channel = {"Stable": self._rel("v0.3.312", "0.3.311")}
        self.assertEqual(rc.channel_zero_match_warnings(by_channel), [])

    def test_empty_channel_is_not_flagged(self):
        # Zero releases is not a label-scheme mismatch, so do not warn.
        self.assertEqual(rc.channel_zero_match_warnings({"Beta": []}), [])

    def test_flags_multiple_channels(self):
        by_channel = {
            "Stable": self._rel("v1.0.0"),
            "Beta": self._rel("2024.01.01-rc1"),
            "Unstable": self._rel("0.3.1"),
        }
        self.assertEqual(
            sorted(rc.channel_zero_match_warnings(by_channel)), ["Beta", "Stable"]
        )


class ChannelCountsTests(unittest.TestCase):
    def _rel(self, *semvers):
        return [{"semver": s} for s in semvers]

    def test_counts_split_selected_by_existing(self):
        by_channel = {"Stable": self._rel("0.3.312", "0.3.311", "v0.3.310", "")}
        counts = rc.channel_counts(by_channel, existing={"0.3.311"}, limit=20)
        c = counts["Stable"]
        self.assertEqual(c["seen"], 4)
        self.assertEqual(c["selected"], 2)      # 0.3.312, 0.3.311
        self.assertEqual(c["non_bare"], 1)      # v0.3.310
        self.assertEqual(c["empty"], 1)         # ""
        self.assertEqual(c["existed"], 1)       # 0.3.311 already present
        self.assertEqual(c["to_create"], 1)     # 0.3.312 is new

    def test_counts_respect_limit_window(self):
        by_channel = {"Unstable": self._rel("0.3.3", "0.3.2", "0.3.1")}
        counts = rc.channel_counts(by_channel, existing=set(), limit=2)
        c = counts["Unstable"]
        self.assertEqual(c["seen"], 3)          # seen reflects everything fetched
        self.assertEqual(c["selected"], 2)      # only the window is selected
        self.assertEqual(c["to_create"], 2)


class RenderSummaryTests(unittest.TestCase):
    def test_summary_lists_created_versions(self):
        counts = {"Stable": {"seen": 2, "selected": 2, "non_bare": 0,
                             "empty": 0, "existed": 1, "to_create": 1}}
        md = rc.render_summary(counts, ["0.3.312"], dry_run=False)
        self.assertIn("| Stable | 2 | 2 | 0 | 1 | 1 |", md)
        self.assertIn("created (1)", md)
        self.assertIn("`0.3.312`", md)

    def test_dry_run_uses_would_create_wording(self):
        counts = {"Beta": {"seen": 1, "selected": 1, "non_bare": 0,
                           "empty": 0, "existed": 0, "to_create": 1}}
        md = rc.render_summary(counts, ["0.4.0"], dry_run=True)
        self.assertIn("dry run", md)
        self.assertIn("would create (1)", md)

    def test_summary_when_nothing_to_create(self):
        counts = {"Stable": {"seen": 1, "selected": 1, "non_bare": 0,
                             "empty": 0, "existed": 1, "to_create": 0}}
        md = rc.render_summary(counts, [], dry_run=False)
        self.assertIn("No branches to create", md)


class EnvIntTests(unittest.TestCase):
    def test_parses_valid_int(self):
        os.environ["_TEST_RELEASE_LIMIT"] = "5"
        try:
            self.assertEqual(rc._env_int("_TEST_RELEASE_LIMIT", "20"), 5)
        finally:
            del os.environ["_TEST_RELEASE_LIMIT"]

    def test_uses_default_when_unset(self):
        os.environ.pop("_TEST_RELEASE_LIMIT_MISSING", None)
        self.assertEqual(rc._env_int("_TEST_RELEASE_LIMIT_MISSING", "20"), 20)

    def test_fails_loudly_on_non_numeric(self):
        os.environ["_TEST_RELEASE_LIMIT"] = "twenty"
        try:
            with self.assertRaises(SystemExit):
                rc._env_int("_TEST_RELEASE_LIMIT", "20")
        finally:
            del os.environ["_TEST_RELEASE_LIMIT"]


class PlanBranchesTests(unittest.TestCase):
    def test_skips_existing_creates_new(self):
        selected = ["0.3.166", "0.3.310", "0.3.312"]
        existing = {"0.3.310", "main", "docs/foo"}
        to_create, to_skip = rc.plan_branches(selected, existing)
        self.assertEqual(to_create, ["0.3.166", "0.3.312"])
        self.assertEqual(to_skip, ["0.3.310"])

    def test_idempotent_when_all_exist(self):
        selected = ["0.3.310", "0.3.312"]
        existing = {"0.3.310", "0.3.312"}
        to_create, to_skip = rc.plan_branches(selected, existing)
        self.assertEqual(to_create, [])
        self.assertEqual(to_skip, ["0.3.310", "0.3.312"])

    def test_all_new_when_none_exist(self):
        to_create, to_skip = rc.plan_branches(["0.3.312"], set())
        self.assertEqual(to_create, ["0.3.312"])
        self.assertEqual(to_skip, [])


if __name__ == "__main__":
    unittest.main()
