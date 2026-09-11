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
import json
import os
import tempfile
import unittest
from unittest import mock

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

    def test_footnote_when_per_channel_sum_exceeds_deduped_total(self):
        # 0.3.312 is live on both channels: per-channel "To create" sums to 2,
        # but it is created once, so the deduped total is 1. Footnote must fire.
        counts = {
            "Stable": {"seen": 1, "selected": 1, "non_bare": 0,
                       "empty": 0, "existed": 0, "to_create": 1},
            "Beta": {"seen": 1, "selected": 1, "non_bare": 0,
                     "empty": 0, "existed": 0, "to_create": 1},
        }
        md = rc.render_summary(counts, ["0.3.312"], dry_run=False)
        self.assertIn("created (1)", md)
        self.assertIn("sums to 2", md)
        self.assertIn("counted once per channel", md)

    def test_no_footnote_when_counts_agree(self):
        # No cross-channel duplication: per-channel sum equals the deduped total,
        # so no reconciling footnote should appear.
        counts = {
            "Stable": {"seen": 1, "selected": 1, "non_bare": 0,
                       "empty": 0, "existed": 0, "to_create": 1},
            "Beta": {"seen": 1, "selected": 1, "non_bare": 0,
                     "empty": 0, "existed": 0, "to_create": 1},
        }
        md = rc.render_summary(counts, ["0.3.312", "0.4.0"], dry_run=False)
        self.assertNotIn("sums to", md)


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


class RunApplyTests(unittest.TestCase):
    def test_creates_one_branch_per_version(self):
        created = []
        with mock.patch.object(rc, "get_base_sha", return_value="abc1234def"), \
                mock.patch.object(rc, "create_branch",
                                  side_effect=lambda repo, name, sha, token: created.append(name) or True):
            n = rc.run_apply(["0.3.166", "0.3.312"], "acme/docs", "main", "tok")
        self.assertEqual(n, 2)
        self.assertEqual(created, ["0.3.166", "0.3.312"])

    def test_existing_ref_race_counts_as_skip_not_created(self):
        # create_branch returns False when the ref already exists (422 race).
        with mock.patch.object(rc, "get_base_sha", return_value="abc1234def"), \
                mock.patch.object(rc, "create_branch", return_value=False):
            n = rc.run_apply(["0.3.312"], "acme/docs", "main", "tok")
        self.assertEqual(n, 0)

    def test_empty_plan_does_no_work(self):
        # No base SHA lookup, no creation -- apply must be a no-op on an empty plan.
        with mock.patch.object(rc, "get_base_sha") as base, \
                mock.patch.object(rc, "create_branch") as create:
            n = rc.run_apply([], "acme/docs", "main", "tok")
        self.assertEqual(n, 0)
        base.assert_not_called()
        create.assert_not_called()

    def test_rejects_non_bare_version_in_handoff(self):
        # A malformed hand-off (e.g. a v-prefixed label) must fail loudly, never
        # create a junk ref.
        with mock.patch.object(rc, "get_base_sha", return_value="abc1234def"), \
                mock.patch.object(rc, "create_branch") as create:
            with self.assertRaises(ValueError):
                rc.run_apply(["v0.3.312"], "acme/docs", "main", "tok")
        create.assert_not_called()


class RunPlanTests(unittest.TestCase):
    def _run_plan_with_fakes(self, releases_by_channel, existing, output_path):
        """Drive run_plan with the network stubbed out; returns to_create."""
        env = {"GITHUB_OUTPUT": output_path}
        with mock.patch.object(rc, "resolve_app_id", return_value="app-1"), \
                mock.patch.object(rc, "resolve_channel_ids",
                                  return_value={name: f"cid-{name}" for name in releases_by_channel}), \
                mock.patch.object(rc, "fetch_channel_releases",
                                  side_effect=lambda app_id, cid, tok, limit: releases_by_channel[
                                      cid.replace("cid-", "")]), \
                mock.patch.object(rc, "list_existing_branches", return_value=set(existing)), \
                mock.patch.dict(os.environ, env, clear=False):
            return rc.run_plan(
                app_slug="acme", vendor_token="v", github_token="g", repo="acme/docs",
                base_branch="main", channels=list(releases_by_channel), limit=20, dry_run=False,
            )

    def test_dedupes_across_channels_and_skips_existing(self):
        by_channel = {
            "Stable": [{"semver": "0.3.166"}, {"semver": "0.3.312"}],
            "Beta": [{"semver": "0.3.312"}],  # dup across channels -> planned once
        }
        with tempfile.NamedTemporaryFile("w+", delete=False) as fh:
            out = fh.name
        try:
            to_create = self._run_plan_with_fakes(by_channel, existing={"0.3.166"}, output_path=out)
            self.assertEqual(to_create, ["0.3.312"])  # 0.3.166 exists, 0.3.312 deduped
            with open(out) as f:
                emitted = f.read()
        finally:
            os.unlink(out)
        # Plan hands the list to the apply job as JSON on the to_create output.
        self.assertIn("to_create=", emitted)
        line = next(l for l in emitted.splitlines() if l.startswith("to_create="))
        self.assertEqual(json.loads(line[len("to_create="):]), ["0.3.312"])

    def test_fails_loudly_when_no_channel_resolves(self):
        with mock.patch.object(rc, "resolve_app_id", return_value="app-1"), \
                mock.patch.object(rc, "resolve_channel_ids", return_value={}):
            with self.assertRaises(SystemExit):
                rc.run_plan("acme", "v", "g", "acme/docs", "main", ["Nope"], 20, False)


class MainDispatchTests(unittest.TestCase):
    def test_apply_mode_reads_to_create_env_and_applies(self):
        applied = {}
        env = {
            "MODE": "apply",
            "GITHUB_TOKEN": "g",
            "GITHUB_REPOSITORY": "acme/docs",
            "BASE_BRANCH": "main",
            "TO_CREATE": json.dumps(["0.3.312", "0.4.0"]),
        }
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(rc, "run_apply", return_value=2) as apply:
            rc.main()
        apply.assert_called_once()
        # First positional arg is the parsed to_create list.
        self.assertEqual(apply.call_args.args[0], ["0.3.312", "0.4.0"])

    def test_apply_mode_defaults_empty_plan(self):
        # No TO_CREATE in the env -> apply gets an empty plan, not a crash.
        env = {"MODE": "apply", "GITHUB_TOKEN": "g", "GITHUB_REPOSITORY": "acme/docs",
               "TO_CREATE": ""}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(rc, "run_apply", return_value=0) as apply:
            os.environ.pop("TO_CREATE")  # simulate the var being absent
            rc.main()
        self.assertEqual(apply.call_args.args[0], [])

    def test_rejects_unknown_mode(self):
        with mock.patch.dict(os.environ, {"MODE": "bogus"}, clear=False):
            with self.assertRaises(SystemExit):
                rc.main()


if __name__ == "__main__":
    unittest.main()
