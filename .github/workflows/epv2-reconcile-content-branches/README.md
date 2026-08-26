# EPv2 Reconcile Content Branches

A reusable GitHub Actions workflow that auto-creates the per-release content branches
Enterprise Portal v2 (EPv2) needs, so new Replicated releases stop 404-ing in your portal.

If you run EPv2 with the "Require matching content for release versions" toggle on, you
know the drill: every release needs a matching branch in your docs repo, and
hand-creating them is easy to forget. Drop this workflow into your docs repo and it keeps
up for you.

## What it does

EPv2 links docs content to a release only when a real git branch in your docs repo is
named exactly the bare release version label. That means `0.3.312`, not `v0.3.312` and not
`release/v0.3.312`. With the toggle on, a release with no matching branch 404s in the
portal. That exact-match rule is the whole contract, and it comes from the vendor API code
in vandoor (`handlers/vendor-api/replv3/enterprise_portal/content_version_pin.go`,
`GetVersionByRepoAndBranch(ctx, repoID, branch)`). This workflow encodes that rule and
unit-tests it, so the branches it creates always match what the portal looks for.

When it runs, the workflow:

1. Reads the releases on your configured channels from the Replicated vendor API.
2. Keeps only bare release versions and skips pre-releases like `0.3.153-pr.106`.
3. Creates a branch for any version that does not already have one, cut from your base branch.

It only ever creates branches. It never force-updates and it never deletes.

## Quick start

Add a workflow to your docs repo, for example
`.github/workflows/reconcile-content-branches.yml`. This example fires when a GitHub
release is published, with a cron fallback so nothing slips through and a manual dry-run
option:

```yaml
name: Reconcile EPv2 content branches

on:
  release:
    types: [published]
  schedule:
    # Fallback so nothing slips through if a release did not trigger the workflow.
    - cron: "17 */6 * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Log the plan without creating branches"
        type: boolean
        default: false

# Required: the workflow creates release branches in this repo.
permissions:
  contents: write

concurrency:
  group: epv2-reconcile-content-branches
  cancel-in-progress: false

jobs:
  reconcile:
    uses: replicatedhq/reusable-workflows/.github/workflows/epv2-reconcile-content-branches.yaml@main
    with:
      app_slug: your-app-slug
      workflow_ref: main
      dry_run: ${{ github.event.inputs.dry_run || false }}
    secrets:
      replicated_api_token: ${{ secrets.REPLICATED_API_TOKEN }}
```

What each required piece is doing:

- `permissions: contents: write` lets the workflow create branches. Without it, it can read
  your releases but cannot create anything. The reusable workflow also declares
  `contents: write` on its job as a least-privilege floor, but a reusable workflow's
  permissions can only restrict what you granted, so you still grant it here.
- `app_slug` is your Replicated app slug (the value in your vendor portal URL). It is not a
  secret, so it lives in `with:`.
- `workflow_ref` is the ref this workflow fetches its reconcile script from, and it must be
  the same ref you pin `uses:` to. It is required and has no default. See
  [Pinning](#pinning-to-a-specific-version) for why.
- `replicated_api_token` is the only secret you need: a vendor API token with read access
  to your apps, channels, and releases. Add it to your repo secrets.

Start with `dry_run: true` on a manual run if you want to see the plan before it creates
anything. The plan lands in the job summary, and every decision is logged.

## Inputs

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `app_slug` | string | yes | none | Replicated app slug (or name/id) whose releases drive the content branches |
| `workflow_ref` | string | yes | none | Ref of `replicatedhq/reusable-workflows` to fetch the reconcile script from. Pass the same ref you pin the workflow `uses:` to. |
| `channels` | string | no | `Stable,Beta` | Comma-separated channels to reconcile. Unstable is opt-in. |
| `release_limit` | number | no | `20` | Max releases to consider per channel, newest first (bounds the branch set) |
| `base_branch` | string | no | `main` | Branch new content branches are cut from |
| `dry_run` | boolean | no | `false` | Log the plan without creating any branches |

## Secrets

| Name | Required | Description |
|------|----------|-------------|
| `replicated_api_token` | yes | Vendor API token with read access to apps, channels, and releases |

The workflow uses the calling repo's built-in `GITHUB_TOKEN` to create branches, so you do
not supply a GitHub token. That is why `permissions: contents: write` is required on your
calling job.

## Pinning to a specific version

Reference the workflow by SHA and pass the same SHA as `workflow_ref`:

```yaml
jobs:
  reconcile:
    uses: replicatedhq/reusable-workflows/.github/workflows/epv2-reconcile-content-branches.yaml@<sha>
    with:
      app_slug: your-app-slug
      workflow_ref: <sha>
    secrets:
      replicated_api_token: ${{ secrets.REPLICATED_API_TOKEN }}
```

The workflow YAML you run comes from the `uses:` ref, but the reconcile script is a
separate file fetched at `workflow_ref`. If those two drift, you would be running one
version of the workflow against a different version of the script. To keep you from doing
that by accident, `workflow_ref` has no default, so you must name the ref every time. Pin
both to the same value and you are safe.

One caveat worth naming: GitHub only exposes the caller's top-level trigger ref to a
reusable workflow, not the reusable workflow's own `uses:` ref, so the workflow cannot
auto-derive the right `workflow_ref` for you and it cannot verify that the value you passed
matches your pin. That is why you pass it explicitly, and why keeping the two in sync is on
you. The workflow rejects an empty `workflow_ref` and prints both refs (the one it is
fetching from and the caller's trigger ref) as a notice in the run, so you can eyeball a
skew, but it will not, and cannot, hard-fail on one, because the trigger ref does not prove
anything about how you pinned `uses:`.

## The model: coverage, not mirroring

Here is the model to keep in your head. It is a coverage job, not a mirroring job.

- `main` (your base branch) is your canonical working docs branch. It is the base new
  release branches are cut from. It does not track, mirror, or represent any single release
  or channel. With several channels live (Stable, Beta, Unstable, whatever custom ones you
  run), there is no one "latest release" for it to be, and the portal never asks for one.
- The job is coverage: make sure a content branch exists for every release across your
  configured channels. In EPv2 each customer sees the docs for the release their license is
  pinned to, so every release a customer could land on needs a branch.
- The workflow only ever creates branches. Once a branch exists, it is yours.

If you are wondering how to set the docs version a customer sees by default, that is a
portal setting, not something this workflow touches. It does not mutate `main` to steer a
default.

## Caveats

### Timing: when the branch gets cut

A release branch is cut from `main` as it exists when reconcile runs. The branch is not a
point-in-time snapshot of what shipped with that release. If `main` moved on before
reconcile ran, the new branch captures the newer `main`, not the docs that were current at
release time.

So the closer reconcile runs to the release, the closer the branch content tracks what
actually shipped. That is why the [quick start](#quick-start) fires on
`release: [published]` with a cron fallback rather than relying on a timer alone. The
sibling [`notify-release`](../notify-release/README.md) workflow fires on the same event,
so you can model your caller on it. If you promote Replicated releases from a workflow, a
step right after the promotion works too. After that first cut, the branch is yours to
customize.

### Version labels must be bare versions

If a channel returns releases but none of them are bare `MAJOR.MINOR.PATCH` versions, the
workflow emits a warning annotation. That usually means a label-scheme mismatch: a `v`
prefix, calver, or build metadata. It is also why the portal keeps 404-ing even though
releases exist. Fix the labels so they are bare versions.

If none of your configured channels resolve at all, the workflow fails the run rather than
quietly reporting success. That is almost always a typo in the `channels` input or a token
that cannot see the app.

### `release_limit` must exceed your live-release count

`release_limit` bounds how many releases per channel the workflow looks at, newest first.
Anything older than the window is skipped (and logged, never silently dropped). Set it
higher than the number of releases still live on each channel, or you will silently miss
coverage for older-but-still-live releases: if a customer is pinned to a release that has
fallen outside the window, its content branch never gets created and the portal keeps
404-ing for them. The default of `20` fits a short support window; if you support releases
going back further than your 20 newest per channel, raise it to comfortably exceed your
longest live-release count.

### Unstable is opt-in

Unstable is off by default on purpose. Every dev release on Unstable would spawn a branch
this workflow never deletes, so leaving it in the default set piles up clutter fast. Add
`Unstable` to `channels` only if you actually serve docs for Unstable releases.

### Cleanup is your job

The workflow only creates branches. It never deletes them. When you de-list or archive a
release, its content branch stays behind until you remove it. That is deliberate: a branch
may hold customizations you care about, and this workflow has no safe way to know. Prune
the branches for retired releases yourself.

## Run summary

Every run writes a summary to the job's GitHub step summary: per channel, how many releases
it saw, how many were selected as bare versions, how many were skipped as non-bare, how
many branches it created, and how many already existed. A dry run folds its plan into that
same summary, so you can review a dry run at a glance without reading the raw log.

## How it works

1. **Report the script ref** by rejecting an empty `workflow_ref` and printing it alongside
   the caller's trigger ref as a notice, so you can eyeball a skew between the workflow and
   the script it fetches.
2. **Checkout your repo** so branch creation targets your docs repo.
3. **Fetch the reconcile script** from `replicatedhq/reusable-workflows` at `workflow_ref`
   (you do not vendor the script yourself).
4. **Run the unit tests** that prove the branch-name rule, before touching any refs.
5. **Reconcile** by reading your releases, planning the missing branches, and creating them
   off `base_branch`. Existing branches are left untouched, and the run summary lands in the
   job summary.

The reconcile script uses only the Python standard library, so there is no `pip install`
step and nothing extra to trust.
