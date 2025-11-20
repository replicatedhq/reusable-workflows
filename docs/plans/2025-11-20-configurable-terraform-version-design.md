# Configurable Terraform Version for Reusable Workflows

**Date:** 2025-11-20
**Status:** Approved

## Overview

Add an optional `terraform_version` input to all three Terraform reusable workflows (terraform-test.yaml, terraform-plan.yaml, terraform-apply.yaml). This allows consumers to specify which Terraform version to use while maintaining backward compatibility with existing consumers using version 1.4.6.

## Goals

- Optional input with default value of `1.4.6` (no breaking changes)
- Independent version control per workflow (test, plan, apply can use different versions)
- Consistent implementation approach across all three workflows

## Implementation Approach

### 1. Standardize terraform-test.yaml

Currently terraform-test.yaml uses Docker containers (`image: hashicorp/terraform:1.4.6`) while the other two workflows use `hashicorp/setup-terraform@v3`. We'll update terraform-test.yaml to use `hashicorp/setup-terraform@v3` for consistency.

**Benefits:**
- Uniform approach across all workflows makes maintenance easier
- Simplifies version parameterization (single input maps directly to action parameter)
- Small performance trade-off (10-20 seconds) is acceptable given existing 5+ minute operations

### 2. Add terraform_version input

Each workflow gets a new optional input:

```yaml
inputs:
  terraform_version:
    description: "Terraform version to use"
    required: false
    type: string
    default: "1.4.6"
```

### 3. Pass input to setup-terraform action

Replace hardcoded version with input reference:

```yaml
- name: Setup terraform
  uses: hashicorp/setup-terraform@v3
  with:
    terraform_version: ${{ inputs.terraform_version }}
```

## Specific Workflow Changes

### terraform-plan.yaml

Changes to `.github/workflows/terraform-plan.yaml`:
- Add `terraform_version` input to the `inputs:` section (lines 3-32)
- Update `hashicorp/setup-terraform@v3` step to use `${{ inputs.terraform_version }}` (line 54)

### terraform-apply.yaml

Changes to `.github/workflows/terraform-apply.yaml`:
- Add `terraform_version` input to the `inputs:` section (lines 3-36)
- Update `hashicorp/setup-terraform@v3` step to use `${{ inputs.terraform_version }}` (line 64)

### terraform-test.yaml

Changes to `.github/workflows/terraform-test.yaml` (more substantial):
- Add `terraform_version` input to the `inputs:` section (lines 3-30)
- **Remove** the `container:` configuration from both `fmt` and `validate` jobs
- **Add** `hashicorp/setup-terraform@v3` step to both jobs
- Update step to use `${{ inputs.terraform_version }}`

**fmt job will have:**
- Checkout repo
- Setup terraform (with configurable version)
- Run `terraform fmt -recursive -check -diff`

**validate job will have:**
- Checkout repo
- Setup terraform (with configurable version)
- Run `terraform init -upgrade`
- Run `terraform validate`

## Testing & Rollout Strategy

### Testing Approach

1. Create a feature branch in reusable-workflows repo
2. Update a consumer workflow to reference the branch:
   ```yaml
   uses: replicatedhq/reusable-workflows/.github/workflows/terraform-test.yaml@feature-branch-name
   ```
3. Test with both default version (1.4.6) and a newer version (e.g., 1.14.0)
4. Verify all three workflows (test, plan, apply) work correctly
5. Once validated, merge to main

### Backward Compatibility

- Existing consumers require zero changes (default: "1.4.6")
- Consumers can opt-in to newer versions by adding `terraform_version` input
- No breaking changes to the workflow interface

### Documentation

- No README updates needed (workflows are self-documenting via input descriptions)
- Input description clearly states it's optional with default

## Consumer Usage Example

Example of how consumers will use the new input:

```yaml
infraStagingTests:
  uses: replicatedhq/reusable-workflows/.github/workflows/terraform-test.yaml@main
  with:
    terraform_version: "1.14.0"  # Optional - defaults to "1.4.6"
    envPath: "replicated-infra-staging"
    csp_iam_account_id_env_name: "AWS_ACCESS_KEY_ID"
    csp_iam_account_secret_env_name: "AWS_SECRET_ACCESS_KEY"
    csp_region_env_name: "AWS_REGION"
    region: "us-east-1"
  secrets:
    iam_account_id: ${{ secrets.INF_STG_IAC_ACCESS_KEY_ID }}
    iam_account_secret: ${{ secrets.INF_STG_IAC_ACCESS_KEY }}
```

Consumers can specify different versions per environment if needed (e.g., staging on 1.14.0, production on 1.4.6 during migration).

## Summary

We're adding a `terraform_version` input (optional, defaults to "1.4.6") to all three Terraform workflows. terraform-test.yaml gets refactored from Docker containers to `hashicorp/setup-terraform@v3` for consistency. This gives consumers flexibility to upgrade Terraform versions per-environment while maintaining full backward compatibility.
