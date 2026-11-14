# CoDoc GitHub Action Workflow Guide

CoDoc packages as a containerized GitHub Action providing automated documentation drift detection, auto-fix pull requests, and review comments.

## Recommended Workflow Configuration

Add `.github/workflows/codoc.yml` to your repository:

```yaml
name: CoDoc Documentation Gate

on:
  pull_request:
    paths:
      - 'src/**'
      - 'docs/**'
  push:
    branches:
      - main
    paths:
      - 'src/**'
      - 'docs/**'

permissions:
  contents: write
  pull-requests: write

jobs:
  codoc:
    name: Check & Repair Documentation
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run CoDoc Action
        uses: ChamHerman/CoDoc@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          llm_api_key: ${{ secrets.OPENAI_API_KEY }}
          confidence_threshold: '0.80'
          fail_on_stale: false
```

## Permissions

The Action requires:
- `contents: write`: To create or update the dedicated `codoc/fix-pr-<number>` auto-fix branch.
- `pull-requests: write`: To open auto-fix PRs and upsert `<!-- codoc-report -->` summary comments on pull requests.

On forks or tokens without write access, CoDoc degrades gracefully to read-only status and exits clean `0`.

## Inputs

| Input | Description | Default |
| :--- | :--- | :--- |
| `github_token` | GitHub access token | `${{ github.token }}` |
| `llm_api_key` | OpenAI API key | `""` |
| `llm_model` | Model name for verification | `gpt-4o-mini` |
| `confidence_threshold` | Minimum confidence score for auto_fix | `0.80` |
| `max_suspects` | Maximum sections evaluated per run | `25` |
| `ignore_paths` | Patterns to ignore | `tests/**,.github/**,migrations/**` |
| `auto_merge` | Enable auto-merge on fix PRs | `false` |
| `fail_on_stale` | Fail CI if stale docs remain | `false` |
