#!/usr/bin/env python3
"""Unit tests for the pure decision logic in epv2_reconcile_content_branches.

Covers the three things the engineering charter calls out:
  * bare-version format (EP v2's exact-match rule),
  * version selection / bounding / pre-release filtering,
  * idempotency (never re-create or force-update an existing branch).

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""
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
