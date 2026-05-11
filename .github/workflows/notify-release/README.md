# Notify Release

A reusable GitHub Actions workflow that posts a Slack notification when a release is published.

## Purpose

Notify a Slack channel when a new release is published, including:
- A link comparing the current release to the previous release
- The actor who triggered the release
- A custom repository name (optional)

## Usage

```yaml
jobs:
  notify:
    uses: replicatedhq/reusable-workflows/.github/workflows/notify-release.yml@main
    with:
      tag: ${{ github.ref_name }}
    secrets:
      slack_webhook: ${{ secrets.SLACK_RELEASE_WEBHOOK }}
```

## Inputs

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `tag` | string | yes | — | Release tag being published (e.g. `3.0.0`, `v1.2.3`) |
| `repo_name` | string | no | *(calling repo name)* | Repository name to display in the Slack message |
| `previous_sha` | string | no | *(auto-discovered)* | Previous release short SHA for compare link. Auto-discovered from the latest release if omitted. |

## Secrets

| Name | Required | Description |
|------|----------|-------------|
| `slack_webhook` | yes | Slack incoming webhook URL for posting release notifications |

## How it works

1. **Resolve repository name** — Uses the provided `repo_name` or falls back to the calling repository's name.
2. **Get current SHA** — Captures the short SHA (7 chars) of the commit being released.
3. **Get previous release SHA** — Queries the GitHub API for the latest release, resolves its tag to an actual commit SHA, and outputs the short SHA.
4. **Select previous SHA** — Prefers the explicit `previous_sha` input; falls back to the auto-discovered SHA.
5. **Build compare URL** — Constructs a GitHub compare URL (`...compare/<prev>...<curr>`) or a releases tag URL if no previous SHA is available.
6. **Post to Slack** — Sends a message via Slack incoming webhook with the release details and compare link.
