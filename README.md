# Reusable workflows for replicatedhq

This repository holds reusable GitHub Actions workflows that can be used across multiple repositories in the  `replicatedhq` organization.

More information on using reusable workflows can be found [here](https://docs.github.com/en/enterprise-cloud@latest/actions/using-workflows/reusing-workflows).

## Usage
1. Create a workflow yaml file under `.github/workflows`. Where possible, use the naming convention `<domain>-<action>.yaml`, e.g. `pr-enforce-labels.yaml`. \[[docs](https://docs.github.com/en/enterprise-cloud@latest/actions/using-workflows/reusing-workflows#example-reusable-workflow)\] Example workflow:
```yaml
on:
  workflow_call:
    inputs:
      greeting:
        description: 'A greeting to display'
        required: true
  jobs:
    say-hello:
      runs-on: ubuntu-latest
      steps:
        - name: Greet
          run: |
            echo "${{ inputs.greeting }}" 
```
  - Reusable workflows are triggered with the `workflow_call` event \[[docs](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#onworkflow_call)\].
  - Inputs and secrets should be used to parameterize the workflow to maximize the reuse potential.
    - Secrets are not passed to called workflows by default. In order to pass secrets, they must either be defined in the workflow, or `secrets: inherit` must be set on the caller.
  - Prefer using specific versions for `runs-on` instead of `ubuntu-latest`. Since these workflows may be used in multiple repositories, any breaking behavior introduced in a new runner image may have a wide blast radius.
2. Add a job to the calling workflow in the repository where you would like to run the reusable workflow. \[[docs](https://docs.github.com/en/enterprise-cloud@latest/actions/using-workflows/reusing-workflows#calling-a-reusable-workflow)\] Example:
```yaml
jobs:
  greet:
    uses: replicatedhq/reusable-workflows/.github/workflows/say-hello.yaml@main
    with:
      greeting: Hi there!
```
  - To pin to a specific version of the repo, add the GitHub SHA ref to the `uses` field, e.g. `replicatedhq/reusable-workflows/.github/workflows/say-hello.yaml@1a2b3c4`
    - There are no current plans to tag versions in this repo.
