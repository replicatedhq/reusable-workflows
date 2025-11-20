# Implementation Plan: Configurable Terraform Version

**Date:** 2025-11-20
**Design Doc:** [2025-11-20-configurable-terraform-version-design.md](./2025-11-20-configurable-terraform-version-design.md)

## Implementation Steps

### 1. Update terraform-plan.yaml

**File:** `.github/workflows/terraform-plan.yaml`

**Step 1a: Add terraform_version input**
- Location: Lines 3-32 (inputs section)
- Add after the `runner` input:
```yaml
      terraform_version:
        description: "Terraform version to use"
        required: false
        type: string
        default: "1.4.6"
```

**Step 1b: Update setup-terraform step**
- Location: Line 51-54
- Change from:
```yaml
      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.4.6
```
- Change to:
```yaml
      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: ${{ inputs.terraform_version }}
```

### 2. Update terraform-apply.yaml

**File:** `.github/workflows/terraform-apply.yaml`

**Step 2a: Add terraform_version input**
- Location: Lines 3-36 (inputs section)
- Add after the `environment` input:
```yaml
      terraform_version:
        description: "Terraform version to use"
        required: false
        type: string
        default: "1.4.6"
```

**Step 2b: Update setup-terraform step**
- Location: Line 61-64
- Change from:
```yaml
      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.4.6
```
- Change to:
```yaml
      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: ${{ inputs.terraform_version }}
```

### 3. Update terraform-test.yaml

**File:** `.github/workflows/terraform-test.yaml`

**Step 3a: Add terraform_version input**
- Location: Lines 3-30 (inputs section)
- Add after the `region` input:
```yaml
      terraform_version:
        description: "Terraform version to use"
        required: false
        type: string
        default: "1.4.6"
```

**Step 3b: Refactor fmt job**
- Location: Lines 46-56
- Change from:
```yaml
  fmt:
    runs-on: ubuntu-latest
    container:
      image: hashicorp/terraform:1.4.6

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Run terraform fmt
        run: terraform fmt -recursive -check -diff
```
- Change to:
```yaml
  fmt:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: ${{ inputs.terraform_version }}

      - name: Run terraform fmt
        run: terraform fmt -recursive -check -diff
```

**Step 3c: Refactor validate job**
- Location: Lines 58-75
- Change from:
```yaml
  validate:
    runs-on: ubuntu-latest
    container:
      image: hashicorp/terraform:1.4.6

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Run terraform validate
        env:
          ${{ inputs.csp_iam_account_id_env_name }}: ${{ secrets.iam_account_id }}
          ${{ inputs.csp_iam_account_secret_env_name }}: ${{ secrets.iam_account_secret }}
          ${{ inputs.csp_region_env_name }}: ${{ inputs.region }}
        run: |
             cd ${{ inputs.envPath }}
             terraform init -upgrade
             terraform validate
```
- Change to:
```yaml
  validate:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: ${{ inputs.terraform_version }}

      - name: Run terraform validate
        env:
          ${{ inputs.csp_iam_account_id_env_name }}: ${{ secrets.iam_account_id }}
          ${{ inputs.csp_iam_account_secret_env_name }}: ${{ secrets.iam_account_secret }}
          ${{ inputs.csp_region_env_name }}: ${{ inputs.region }}
        run: |
             cd ${{ inputs.envPath }}
             terraform init -upgrade
             terraform validate
```

## Verification

After implementation, verify:
1. All three workflow files have valid YAML syntax
2. The `terraform_version` input is properly defined in all three workflows
3. All `setup-terraform` steps reference `${{ inputs.terraform_version }}`
4. terraform-test.yaml no longer uses container-based approach

## Testing Plan

1. Create feature branch with changes
2. Update a consumer workflow to use the branch
3. Test with default version (omit `terraform_version` input)
4. Test with explicit version (e.g., `terraform_version: "1.14.0"`)
5. Verify all three workflows execute successfully
6. Merge to main once validated

## Summary

Total changes:
- 3 workflow files modified
- 7 specific edits (3 input additions, 2 simple replacements, 2 refactors)
- terraform-test.yaml has the most substantial changes (removing containers, adding setup-terraform)
- All changes are backward compatible (default: "1.4.6")
