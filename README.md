# reusable-workflows

## How to use (WIP)
1. All reusable workflows must be created under `.github/workflows/`
    - Subdirectories are not supported.
2. Naming convention is up for discussion, but current workflows are named as follows:
```
.github/workflows/<tool>-<action>.yaml
```
* To run a container scan, we can use something like:
```
.github/workflows/trivy-scan.yaml
```
3. Keep the workflows as sanitized as possible as this is a public repo and we don't want any internal information here.
- Use inputs and secrets where possible.
    - Keep the names of these as agnostic as possible
        - These can be more more specific once pulled into your project repo

