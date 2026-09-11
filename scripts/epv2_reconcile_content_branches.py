#!/usr/bin/env python3
"""Reconcile Enterprise Portal v2 content branches for a Replicated app's releases.

Enterprise Portal (EP) v2 links docs content to a release ONLY when a real git
branch in the docs repo is named EXACTLY the release version label -- the bare
version string, e.g. ``0.3.312`` (NOT ``v0.3.312`` and NOT ``release/v0.3.312``).

Verified in vandoor (replicatedhq/vandoor):
  handlers/vendor-api/replv3/enterprise_portal/content_version_pin.go
  -> GetVersionByRepoAndBranch(ctx, repoID, branch)
  -> error: "version label %q matches a real git branch"

The portal toggle "Require matching content for release versions" is ON, so a
release version with no matching branch 404s in the portal. This script creates
those branches so every release a customer could be pinned to has content.

The model (single source of truth for what this workflow is for):
  * ``main`` (the base branch) is the vendor's canonical working docs branch. It
    is the base that new release branches are cut from. It does NOT track or
    mirror any single release or channel -- with multiple channels there is no
    one "latest release" to track, and the portal never needs one.
  * The job is COVERAGE: ensure a content branch EXISTS for every release across
    the configured channels. In EP v2 each customer sees the docs for the release
    their license is on, so every release a customer could be pinned to needs a
    branch. That is the requirement, not a single "latest".
  * Each release branch is cut from ``main`` as it exists WHEN RECONCILE RUNS.
    It is not a point-in-time snapshot of what shipped -- if reconcile runs after
    ``main`` moved on, the branch captures the newer ``main``. See the README's
    timing section; run reconcile close to release time to keep branches aligned
    with what shipped.
  * A branch is created once and NEVER force-updated. The vendor owns it after
    that -- they can edit it if a release's content needs to diverge, and sync
    from ``main`` themselves.
  * This workflow only ever CREATES branches. It never deletes. Cleaning up
    branches for de-listed or archived releases is the vendor's job.

Strategy (idempotent, bounded):
  1. Resolve the app id from the app slug (REPLICATED_APP).
  2. For each configured channel (default Stable,Beta -- Unstable is opt-in),
     fetch the latest N releases from the Replicated vendor API.
  3. Keep only bare release versions (skip pre-releases like ``0.3.153-pr.106``).
  4. Skip versions whose branch already exists (never force-update).
  5. Create the remaining branches off the base branch (default ``main``).

Fail-loudly posture:
  * If a channel returned releases but ZERO of them are bare versions, that is a
    likely label-scheme mismatch (v-prefix, calver, build metadata). Emit a
    ``::warning::`` annotation so it surfaces in the run, rather than silently
    creating nothing.
  * If NONE of the configured channels resolve at all, that is total
    misconfiguration -- exit non-zero rather than reporting success.
  * A per-channel run summary (releases seen / selected / created / skipped) is
    written to ``$GITHUB_STEP_SUMMARY`` so a run (dry run included) is reviewable
    at a glance.

The pure decision logic (is_release_version / branch_name / select_versions /
plan_branches / channel_zero_match_warnings / render_summary) has no I/O and is
unit-tested in test_epv2_reconcile_content_branches.py. The I/O helpers use only
the Python standard library, so the workflow needs no pip install step.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

VENDOR_API_BASE = "https://api.replicated.com/vendor/v3"
GITHUB_API_BASE = "https://api.github.com"

# Both APIs reject urllib's default agent (WAF 403 / GitHub requires a UA).
USER_AGENT = "replicatedhq-reusable-workflows-epv2-reconcile-content-branches"

# No single API call should be able to hang the whole job. urllib's default is no
# timeout at all, so a stalled vendor/GitHub call would block until the job's own
# timeout fires.
HTTP_TIMEOUT = 30  # seconds

# A bare release version: MAJOR.MINOR.PATCH, digits and dots only.
# Deliberately rejects a leading "v", any "-" pre-release suffix (e.g. PR
# releases "0.3.153-pr.106"), and any "+" build metadata -- EP v2 matches the
# bare version label exactly.
_BARE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


# --------------------------------------------------------------------------- #
# Pure logic (no I/O) -- unit tested.
# --------------------------------------------------------------------------- #
def is_release_version(semver: str) -> bool:
    """True if ``semver`` is a bare release version EP v2 can match on."""
    return bool(_BARE_VERSION_RE.match((semver or "").strip()))


def branch_name(version: str) -> str:
    """The content branch name for a release version.

    This is the single source of truth for EP v2's exact-match rule: the branch
    name is the *bare* version, with no ``v`` prefix and no ``release/`` prefix.
    """
    name = (version or "").strip()
    if not is_release_version(name):
        raise ValueError(f"refusing to derive a branch name from non-bare version {version!r}")
    return name


def select_versions(
    releases_by_channel: dict[str, list[dict]], limit: int
) -> tuple[list[str], list[str]]:
    """Choose which release versions need a content branch.

    Args:
        releases_by_channel: mapping of channel name -> list of release dicts,
            newest first, each with a ``semver`` key (as the vendor API returns).
        limit: max releases to consider per channel (bounds the branch set so we
            never fan out over all historical releases).

    Returns:
        (selected, skipped) where ``selected`` is a sorted list of bare version
        strings and ``skipped`` is a list of human-readable log lines explaining
        every release that was NOT selected.
    """
    selected = set()
    skipped = []
    for channel, releases in releases_by_channel.items():
        releases = releases or []
        considered = releases[:limit]
        for extra in releases[limit:]:
            skipped.append(
                f"{channel}: {extra.get('semver', '?')} skipped (beyond latest {limit})"
            )
        for release in considered:
            semver = (release.get("semver") or "").strip()
            if not semver:
                skipped.append(f"{channel}: release with empty semver skipped")
            elif is_release_version(semver):
                selected.add(semver)
            else:
                skipped.append(f"{channel}: {semver} skipped (not a bare release version)")
    return sorted(selected, key=_version_key), skipped


def plan_branches(
    selected: list[str], existing: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Split desired versions into (to_create, to_skip_existing).

    Idempotency lives here: any version whose branch already exists is skipped,
    never re-created and never force-updated.
    """
    existing = set(existing)
    to_create = [v for v in selected if branch_name(v) not in existing]
    to_skip = [v for v in selected if branch_name(v) in existing]
    return to_create, to_skip


def channel_zero_match_warnings(releases_by_channel: dict[str, list[dict]]) -> list[str]:
    """Channels that returned releases but ZERO bare-version matches.

    Returns a list of channel names. Each one is a likely label-scheme mismatch
    (v-prefix, calver, build metadata) that would otherwise create zero branches
    while the portal keeps 404-ing -- the caller turns each into a ``::warning::``.
    """
    offenders = []
    for channel, releases in releases_by_channel.items():
        releases = releases or []
        if not releases:
            continue
        if not any(is_release_version((r.get("semver") or "").strip()) for r in releases):
            offenders.append(channel)
    return offenders


def channel_counts(
    releases_by_channel: dict[str, list[dict]], existing: Iterable[str], limit: int
) -> dict[str, dict[str, int]]:
    """Per-channel tallies for the run summary.

    Returns a dict of channel -> {seen, selected, non_bare, empty, existed,
    to_create}, computed from the same rules the real plan uses so the summary
    can never disagree with what the run actually did.

    ``selected`` counts bare versions within the limit window (before dedupe
    across channels). ``to_create`` and ``existed`` are that channel's selected
    versions split by whether their branch already exists.
    """
    existing = set(existing)
    counts = {}
    for channel, releases in releases_by_channel.items():
        releases = releases or []
        window = releases[:limit]
        seen = len(releases)
        selected = non_bare = empty = existed = to_create = 0
        for release in window:
            semver = (release.get("semver") or "").strip()
            if not semver:
                empty += 1
            elif is_release_version(semver):
                selected += 1
                if branch_name(semver) in existing:
                    existed += 1
                else:
                    to_create += 1
            else:
                non_bare += 1
        counts[channel] = {
            "seen": seen,
            "selected": selected,
            "non_bare": non_bare,
            "empty": empty,
            "existed": existed,
            "to_create": to_create,
        }
    return counts


def render_summary(
    counts: dict[str, dict[str, int]], to_create_versions: list[str], dry_run: bool
) -> str:
    """Render the run summary as GitHub-flavored markdown.

    Pure string builder so it is unit-testable. ``to_create_versions`` is the
    deduped, cross-channel list actually planned for creation; the dry-run plan
    folds into this same summary so a dry run is reviewable at a glance.
    """
    verb = "would create" if dry_run else "created"
    lines = []
    lines.append("## EPv2 content-branch reconcile" + (" (dry run)" if dry_run else ""))
    lines.append("")
    lines.append("| Channel | Releases seen | Bare selected | Non-bare skipped | To create | Already existed |")
    lines.append("|---|---|---|---|---|---|")
    for channel in sorted(counts):
        c = counts[channel]
        lines.append(
            f"| {channel} | {c['seen']} | {c['selected']} | {c['non_bare']} "
            f"| {c['to_create']} | {c['existed']} |"
        )
    lines.append("")
    if to_create_versions:
        lines.append(f"**Branches {verb} ({len(to_create_versions)}):** "
                     + ", ".join(f"`{v}`" for v in to_create_versions))
    else:
        lines.append("**No branches to create** -- every selected release already has a content branch.")
    # The per-channel "To create" column counts a version once per channel it
    # appears on, so summing it double-counts a version live on two channels. The
    # deduped total above is the real branch count; note the gap so the columns
    # never look like they disagree.
    per_channel_to_create = sum(c["to_create"] for c in counts.values())
    if per_channel_to_create > len(to_create_versions):
        lines.append("")
        lines.append(
            f"> The per-channel **To create** column sums to {per_channel_to_create}, "
            f"more than the {len(to_create_versions)} branch(es) above, because a "
            "version live on more than one channel is counted once per channel there "
            "but created only once."
        )
    return "\n".join(lines) + "\n"


def _version_key(version: str) -> tuple[int, ...]:
    """Sort key so versions order numerically, not lexically."""
    return tuple(int(p) for p in version.split("."))


# --------------------------------------------------------------------------- #
# I/O helpers (standard library only).
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    """An HTTP error from the vendor or GitHub API, carrying the response body.

    urllib's HTTPError drops the response body unless you read it, leaving only a
    status code in the log. _http_json reads it and re-raises this instead, so:
      * callers can branch on the body (e.g. tell GitHub's benign "Reference
        already exists" 422 from a real one), and
      * an uncaught error prints the API's own message, not a bare status.
    """

    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} from {url}: {body}")


def _http_json(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    body: dict | None = None,
    timeout: int = HTTP_TIMEOUT,
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        # Read the API's error body once so the message survives into the log /
        # the caller, then re-raise it in a form that carries that body.
        try:
            detail = err.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - body already consumed/unavailable
            detail = ""
        raise ApiError(err.code, detail, url) from err
    return json.loads(raw) if raw else {}


def _vendor_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def resolve_app_id(app_slug: str, token: str) -> str:
    data = _http_json(f"{VENDOR_API_BASE}/apps", _vendor_headers(token))
    for app in data.get("apps", []):
        if app.get("slug") == app_slug or app.get("name") == app_slug or app.get("id") == app_slug:
            return app["id"]
    raise SystemExit(f"app slug {app_slug!r} not found in vendor account")


def resolve_channel_ids(
    app_id: str, channel_names: Iterable[str], token: str
) -> dict[str, str]:
    """Return {name: id} for the requested channels; log any that are missing."""
    data = _http_json(f"{VENDOR_API_BASE}/app/{app_id}/channels", _vendor_headers(token))
    by_name = {c.get("name"): c.get("id") for c in data.get("channels", [])}
    resolved = {}
    for name in channel_names:
        if name in by_name:
            resolved[name] = by_name[name]
        else:
            print(f"WARNING: channel {name!r} not found; skipping", file=sys.stderr)
    return resolved


def fetch_channel_releases(app_id: str, channel_id: str, token: str, limit: int) -> list[dict]:
    url = f"{VENDOR_API_BASE}/app/{app_id}/channel/{channel_id}/releases?pageSize={limit}"
    data = _http_json(url, _vendor_headers(token))
    return data.get("releases") or []


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # urllib defaults a request WITH a body to application/x-www-form-urlencoded;
        # our POST bodies are JSON, so set it explicitly or GitHub may reject them.
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def list_existing_branches(repo: str, token: str) -> set[str]:
    """Return the set of branch names in ``repo`` (owner/name), paginating."""
    branches = set()
    page = 1
    while True:
        url = f"{GITHUB_API_BASE}/repos/{repo}/branches?per_page=100&page={page}"
        data = _http_json(url, _github_headers(token))
        if not data:
            break
        branches.update(b["name"] for b in data)
        if len(data) < 100:
            break
        page += 1
    return branches


def get_base_sha(repo: str, base_branch: str, token: str) -> str:
    url = f"{GITHUB_API_BASE}/repos/{repo}/git/ref/heads/{urllib.parse.quote(base_branch)}"
    try:
        data = _http_json(url, _github_headers(token))
    except ApiError as err:
        if err.status == 404:
            raise SystemExit(
                f"base branch {base_branch!r} not found in {repo!r}; set base_branch to "
                "an existing branch (usually your default, 'main')"
            )
        raise
    return data["object"]["sha"]


def create_branch(repo: str, name: str, sha: str, token: str) -> bool:
    """Create refs/heads/<name> at <sha>. Returns True if created, False if it
    already existed (treated as success -- idempotent)."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/git/refs"
    body = {"ref": f"refs/heads/{name}", "sha": sha}
    try:
        _http_json(url, _github_headers(token), method="POST", body=body)
        return True
    except ApiError as err:
        # GitHub returns 422 for several distinct failures -- a bad base SHA
        # ("Object does not exist"), an invalid ref name, AND the benign
        # "Reference already exists" race. Only the last is safe to swallow;
        # anything else is a real error that must surface, or a branch silently
        # never gets created and the portal keeps 404-ing.
        if err.status == 422 and "Reference already exists" in err.body:
            print(f"  {name}: already exists (race), skipped", file=sys.stderr)
            return False
        raise


def _warn(message: str) -> None:
    """Emit a GitHub warning annotation (and to stderr for plain logs)."""
    print(f"::warning::{message}")
    print(f"WARNING: {message}", file=sys.stderr)


def _write_summary(markdown: str) -> None:
    """Append ``markdown`` to $GITHUB_STEP_SUMMARY if the runner set it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(markdown)


def _write_output(name: str, value: str) -> None:
    """Append ``name=value`` to $GITHUB_OUTPUT if the runner set it.

    Mirrors _write_summary. This is how the ``plan`` job hands the branch list to
    the ``apply`` job: plan writes ``to_create=<json>`` here, the workflow maps it
    to a job output, and apply reads it back from the TO_CREATE env var.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #
def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"missing required environment variable {name}")
    return value


def _env_int(name: str, default: str) -> int:
    """Parse an int env var, failing loudly on a non-numeric value."""
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"{name} must be an integer, got {raw!r}")


def run_plan(
    app_slug: str,
    vendor_token: str,
    github_token: str,
    repo: str,
    base_branch: str,
    channels: list[str],
    limit: int,
    dry_run: bool,
) -> list[str]:
    """Read-only planning phase: decide which content branches are missing.

    Resolves the app/channels, fetches releases, warns on label-scheme mismatches,
    selects bare versions (deduped across channels), and diffs against the existing
    branches. Writes the run summary and emits ``to_create`` as a job output so the
    apply phase can consume it. Creates NOTHING -- needs only read access.

    Returns the deduped, sorted list of versions whose branches must be created.
    """
    print(f"Planning content branches for {app_slug!r} in {repo!r}")
    print(f"  channels={channels} limit={limit} base={base_branch} dry_run={dry_run}")

    app_id = resolve_app_id(app_slug, vendor_token)
    channel_ids = resolve_channel_ids(app_id, channels, vendor_token)

    # Total misconfiguration: none of the configured channels exist. Fail loudly
    # rather than reporting success while creating nothing.
    if not channel_ids:
        raise SystemExit(
            f"none of the configured channels {channels} resolved for app {app_slug!r}; "
            "check the channel names against your vendor account"
        )

    releases_by_channel = {}
    for name, cid in channel_ids.items():
        releases = fetch_channel_releases(app_id, cid, vendor_token, limit)
        releases_by_channel[name] = releases
        print(f"  {name}: {len(releases)} release(s) fetched")

    # A channel with releases but zero bare matches is almost always a
    # label-scheme mismatch. Surface it loudly instead of silently doing nothing.
    for channel in channel_zero_match_warnings(releases_by_channel):
        _warn(
            f"channel {channel!r} returned releases but NONE are bare MAJOR.MINOR.PATCH "
            "versions -- no branches will be created for it. Check that its release "
            "labels are bare versions (no 'v' prefix, calver, or build metadata)."
        )

    # Cross-channel dedup lives here: select_versions folds every channel into a
    # single set, so a version live on two channels is planned exactly once. This
    # is why planning stays in one process/job -- splitting channels would break it.
    selected, skipped = select_versions(releases_by_channel, limit)
    for line in skipped:
        print(f"  skip: {line}")

    existing = list_existing_branches(repo, github_token)
    to_create, to_skip = plan_branches(selected, existing)

    for v in to_skip:
        print(f"  exists: branch {v} already present, skipping")

    print(f"Plan: {len(to_create)} branch(es) to create, {len(to_skip)} already present")

    # Write the run summary (dry run folds in here too) before any apply work, so
    # it is present even if branch creation later errors.
    counts = channel_counts(releases_by_channel, existing, limit)
    _write_summary(render_summary(counts, to_create, dry_run))

    # Hand the plan to the apply job. json so the apply side round-trips it back
    # into a list; an empty plan serializes to "[]", which the workflow uses to
    # skip the apply job entirely.
    _write_output("to_create", json.dumps(to_create))

    if not to_create:
        print("Nothing to do -- all release versions already have content branches.")

    return to_create


def run_apply(
    to_create: list[str], repo: str, base_branch: str, github_token: str
) -> int:
    """Mutating apply phase: create the planned content branches.

    Takes the version list the plan produced and creates a branch for each off
    ``base_branch`` as it exists now. Every version is re-validated through
    branch_name() so a malformed hand-off fails loudly rather than creating junk
    refs. Idempotent: an existing ref (race) is treated as success. Needs only
    write access -- no vendor API/token here. Returns the count actually created.
    """
    if not to_create:
        print("Nothing to apply -- no branches were planned.")
        return 0

    base_sha = get_base_sha(repo, base_branch, github_token)
    created = 0
    for v in to_create:
        name = branch_name(v)  # rejects any non-bare version that slipped through
        if create_branch(repo, name, base_sha, github_token):
            print(f"  created branch {name} at {base_sha[:8]} (off {base_branch})")
            created += 1
    print(f"Done: created {created} branch(es).")

    # Record the real outcome. The plan already wrote the table/plan summary; this
    # appends what apply actually did, so a two-job run's summary tells the truth
    # even though the "to create" numbers were written before creation happened.
    _write_summary(f"\n**Apply:** created {created} of {len(to_create)} planned branch(es).\n")
    return created


def main() -> None:
    """Dispatch on MODE: ``plan``, ``apply``, or ``reconcile`` (default).

    The reusable workflow runs the two phases as separate jobs (MODE=plan then
    MODE=apply, with the plan handed over as a job output). MODE=reconcile is the
    default single-process path -- it runs the plan and, unless dry_run, applies it
    in one go, preserving the original standalone/local behavior.
    """
    mode = _env("MODE", "reconcile").lower()

    if mode == "apply":
        github_token = _env("GITHUB_TOKEN", required=True)
        repo = _env("GITHUB_REPOSITORY", required=True)  # e.g. acme/acme-docs
        base_branch = _env("BASE_BRANCH", "main")
        to_create = json.loads(_env("TO_CREATE", "[]"))
        run_apply(to_create, repo, base_branch, github_token)
        return

    if mode not in ("plan", "reconcile"):
        raise SystemExit(f"MODE must be one of plan, apply, reconcile; got {mode!r}")

    app_slug = _env("REPLICATED_APP", required=True)
    vendor_token = _env("REPLICATED_API_TOKEN", required=True)
    github_token = _env("GITHUB_TOKEN", required=True)
    repo = _env("GITHUB_REPOSITORY", required=True)  # e.g. acme/acme-docs
    base_branch = _env("BASE_BRANCH", "main")
    channels = [c.strip() for c in _env("CHANNELS", "Stable,Beta").split(",") if c.strip()]
    limit = _env_int("RELEASE_LIMIT", "20")
    dry_run = _env("DRY_RUN", "false").lower() in ("1", "true", "yes")

    to_create = run_plan(
        app_slug, vendor_token, github_token, repo, base_branch, channels, limit, dry_run
    )

    if mode == "plan":
        return

    # reconcile: apply in the same process, unless this is a dry run.
    if dry_run:
        for v in to_create:
            print(f"  DRY-RUN would create branch {branch_name(v)} off {base_branch}")
        return

    run_apply(to_create, repo, base_branch, github_token)


if __name__ == "__main__":
    try:
        main()
    except ApiError as err:
        # Surface the API's own message (status + body) as a GitHub error
        # annotation instead of dumping a urllib traceback.
        print(f"::error::{err}")
        raise SystemExit(1)
