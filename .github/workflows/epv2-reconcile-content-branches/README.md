# EPv2 Reconcile Content Branches

A reusable GitHub Actions workflow that auto-creates the per-release content
branches Enterprise Portal v2 (EPv2) needs, so new Replicated releases stop
404-ing in your portal.

If you run EPv2 with the "Require matching content for release versions" toggle
on, you already know the drill: every release needs a matching branch in your
docs repo, and hand-creating them is easy to forget. Drop this workflow into your
docs repo and it keeps up for you.

## What it does, and why

EPv2 links docs content to a release only when a real git branch in your docs
repo is named exactly the bare release version label. That means `0.3.312`, not
`v0.3.312` and not `release/v0.3.312`. With the "Require matching content for
release versions" toggle on, a release with no matching branch 404s in the
portal.

That exact-match rule is the whole contract. It comes straight from the vendor
API code in vandoor (`handlers/vendor-api/replv3/enterprise_portal/content_version_pin.go`,
`GetVersionByRepoAndBranch`), which resolves a release's content by looking up a
branch whose name equals the version label. This workflow encodes that rule and
unit-tests it, so the branches it creates always match what the portal looks for.

On a schedule (and on demand), the workflow:

1. Reads the latest releases on your active channels from the Replicated vendor API.
2. Keeps only bare release versions and skips pre-releases like `0.3.153-pr.106`.
3. Creates a branch for any version that does not already have one.

## The branching rule

Here is the model to keep in your head:

- `main` tracks your latest content. Keep it current with your newest release.
- Each new Replicated release gets its own branch, created off `main`.
- Once a branch exists, this workflow never touches it again. No force-updates, ever.

So a release branch is a snapshot you own. If a release needs docs that diverge
from `main`, you are free to edit that branch directly, and to sync changes from
`main` into it yourself whenever you like. The workflow only ever fills in the
branches that are missing; it stays out of the way of the ones you have.

## Usage

Add a workflow to your docs repo, for example
`.github/workflows/reconcile-content-branches.yml`:

```yaml
name: Reconcile EPv2 content branches

on:
  schedule:
    # Every 6 hours. Bounds the portal 404 window without hammering the API.
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
      dry_run: ${{ github.event.inputs.dry_run || false }}
    secrets:
      replicated_api_token: ${{ secrets.REPLICATED_API_TOKEN }}
```

A few notes on that example:

- `permissions: contents: write` is required. Without it, the workflow can read
  your releases but cannot create the branches.
- `app_slug` is your Replicated app slug (the value in your vendor portal URL).
  It is not a secret, so it lives in `with:`.
- The only secret you need is `REPLICATED_API_TOKEN`, a vendor API token with
  read access to your apps, channels, and releases. Add it to your repo secrets.
- Want to pin to a specific version of the reusable workflow? Reference it by SHA,
  and pass the same SHA as `workflow_ref` so the script is fetched from the same
  commit:
  ```yaml
  uses: replicatedhq/reusable-workflows/.github/workflows/epv2-reconcile-content-branches.yaml@<sha>
  with:
    app_slug: your-app-slug
    workflow_ref: <sha>
  ```

Start with `dry_run: true` on a manual run if you want to see the plan before it
creates anything. Every decision is logged, and nothing is truncated silently.

## Inputs

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `app_slug` | string | yes | — | Replicated app slug (or name/id) whose releases drive the content branches |
| `channels` | string | no | `Stable,Beta,Unstable` | Comma-separated active channels to reconcile |
| `release_limit` | number | no | `20` | Max releases to consider per channel (bounds the branch set) |
| `base_branch` | string | no | `main` | Branch new content branches are created off of |
| `dry_run` | boolean | no | `false` | Log the plan without creating any branches |
| `workflow_ref` | string | no | `main` | Ref of `replicatedhq/reusable-workflows` to fetch the reconcile script from. Pin this to match the ref you call the workflow at. |

## Secrets

| Name | Required | Description |
|------|----------|-------------|
| `replicated_api_token` | yes | Vendor API token with read access to apps, channels, and releases |

The workflow uses the calling repo's built-in `GITHUB_TOKEN` to create branches,
so you do not supply a GitHub token. That is why `permissions: contents: write`
is required on your calling job.

## How it works

1. **Checkout your repo** so branch creation targets your docs repo.
2. **Fetch the reconcile script** from `replicatedhq/reusable-workflows` at the
   pinned ref (you do not need to vendor the script yourself).
3. **Run the unit tests** that prove the branch-name rule, before touching any refs.
4. **Reconcile** by reading your releases, planning the missing branches, and
   creating them off `base_branch`. Existing branches are left untouched.

The reconcile script uses only the Python standard library, so there is no
`pip install` step and nothing extra to trust.
