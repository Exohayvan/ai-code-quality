# AI Code Quality

Repository-wide duplication and cyclomatic-complexity quality gates for multi-language projects.

## Quick start

```yaml
name: AI code quality

on:
  pull_request:
  push:
    branches: [main]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - uses: Exohayvan/ai-code-quality@v1
        with:
          level: standard
          require-improvement: "false"
```

The action scans the complete repository, not only changed files. It supports repositories containing multiple languages by combining [jscpd](https://github.com/kucherenko/jscpd) duplication detection with [Lizard](https://github.com/terryyin/lizard) per-function cyclomatic complexity analysis.

`v1` pins jscpd `5.0.14` and Lizard `1.23.0` for reproducible measurements.

## Quality levels

| Level | Maximum duplicated lines | Maximum function CCN |
| --- | ---: | ---: |
| `none` | Disabled | Disabled |
| `minimal` | 20% | 30 |
| `basic` | 15% | 20 |
| `standard` | 10% | 15 |
| `strict` | 0% | 10 |
| `hardened` | 0% | 8 |
| `maximum` | 0% | 5 |

Limits are inclusive. A function exactly at the selected CCN limit passes. A duplication percentage exactly at a nonzero limit also passes. At a 0% duplication limit, any detected duplicated lines fail even if jscpd rounds the displayed percentage to `0.00%`.

The profiles are monotonic: stronger profiles never loosen or disable checks from weaker profiles. In v1, `hardened` and `maximum` tighten complexity further. Additional expensive checks may be added to those profiles in later major-compatible v1 releases, but mutation testing is not part of the initial v1 release.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `level` | `standard` | One of `none`, `minimal`, `basic`, `standard`, `strict`, `hardened`, or `maximum`. |
| `require-improvement` | `false` | Absolute, report-only, maintenance, or percentage-improvement enforcement. |
| `path` | `.` | Repository or subdirectory to analyze. |
| `baseline-ref` | empty | Explicit Git ref for baseline comparisons. |
| `repair-limit` | `15` | Maximum findings placed in the bounded AI repair batch. |
| `annotation-limit` | `40` | Maximum source annotations written to GitHub. |

### `require-improvement`

| Value | Behavior |
| --- | --- |
| `"false"` | Enforce the selected profile's absolute limits immediately. No baseline is used. |
| `"-1"` | Report quality findings without failing the action. Scanner or configuration errors still fail. |
| `"0"` | Permit existing debt but require both measured dimensions not to regress. |
| Positive number | Require both measured dimensions to improve by at least that percentage. |

Improvement is evaluated independently for:

- Duplicated-line percentage
- Complexity debt

Complexity debt is:

```text
sum(max(0, function CCN - selected profile CCN limit))
```

For an improvement percentage `p`, the allowed current duplication is `baseline duplication * (1 - p/100)`. Allowed complexity debt uses the same calculation rounded down to a whole number. A clean baseline remains clean.

Example requiring a 2% improvement against the pull-request merge base:

```yaml
- uses: actions/checkout@v7
  with:
    fetch-depth: 0

- uses: Exohayvan/ai-code-quality@v1
  with:
    level: strict
    require-improvement: "2"
```

### Baseline selection

When `require-improvement` is `0` or a positive number, the action resolves the baseline as follows:

1. `baseline-ref`, when provided
2. The merge base between `HEAD` and the pull request's base commit
3. The `before` commit from a push event

Manual runs and initial branch pushes without a usable previous commit must provide `baseline-ref`. Baseline comparisons require the commit to be present locally, so use `actions/checkout` with `fetch-depth: 0`.

## Outputs

Give the action step an `id` to consume outputs:

```yaml
- uses: Exohayvan/ai-code-quality@v1
  id: quality
  with:
    level: strict
    require-improvement: "false"

- if: always()
  run: |
    echo "Result: ${{ steps.quality.outputs.result }}"
    echo "Duplication: ${{ steps.quality.outputs.duplication-percent }}%"
    echo "Maximum CCN: ${{ steps.quality.outputs.maximum-ccn }}"
```

| Output | Description |
| --- | --- |
| `result` | `pass` or `fail`. |
| `duplication-percent` | Current duplicated-line percentage. |
| `maximum-ccn` | Highest per-function CCN observed. |
| `complexity-debt` | Total complexity debt for the selected profile. |
| `report-path` | Absolute path to the complete JSON report. |
| `fix-context-path` | Absolute path to the bounded AI repair context. |
| `baseline-sha` | Resolved baseline commit when comparison mode is active. |

## Reports for humans and coding agents

The action deliberately avoids dumping every scanner result into the job log.

### GitHub job summary

The bounded summary contains:

- Overall result, profile, and enforcement mode
- Current and allowed duplication
- Current and allowed complexity debt
- Constraints that already pass and should not regress
- Near-limit passing functions
- The highest-priority repair batch
- Paths to complete machine-readable reports

### Source annotations

Failing functions are annotated with the function name, line range, observed CCN, and allowed CCN. Duplicate clone fragments are annotated with their source ranges and stable family identifier. The number of annotations is bounded by `annotation-limit`.

### `.ai-code-quality/fix-context.json`

This compact file is intended for coding agents. It contains:

- A bounded, prioritized `repair_batch`
- Exact paths and line ranges
- Stable finding identifiers
- Metrics and allowed limits
- Passing constraints to preserve
- Near-limit passing functions
- A count of findings omitted from the current batch

Duplicate pairs connected through a shared fragment are grouped into one clone family so a repeated block is presented as one repair problem instead of many pair combinations.

### `.ai-code-quality/report.json`

The complete report contains every function measurement, every duplicate family, check statuses, thresholds, baseline measurements, and enforcement metadata. It is not dumped into the normal log.

Reports remain in the workspace and can be uploaded as artifacts:

```yaml
- uses: Exohayvan/ai-code-quality@v1
  id: quality
  with:
    level: strict

- name: Upload complete quality reports
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: ai-code-quality
    path: |
      ${{ steps.quality.outputs.report-path }}
      ${{ steps.quality.outputs.fix-context-path }}
```

## Scanner behavior

### Duplication

jscpd uses fixed detection sensitivity across every active profile:

- Minimum clone size: 5 lines
- Minimum clone size: 50 tokens
- Comments ignored
- `.gitignore` respected
- Whole target directory scanned
- Symbol case preserved
- Symlinks not followed

The selected profile changes the allowed result, not what counts as a clone.

### Complexity

Lizard analyzes supported source languages and v1 enforces only cyclomatic complexity per function. Function length, parameter count, file length, token count, and average file complexity are not treated as complexity failures.

### Default exclusions

The following directory names are excluded at any depth:

```text
.git
.venv
.ai-code-quality
node_modules
vendor
dist
build
coverage
target
bin
obj
.generated
```

## Runner requirements

The composite action requires:

- Python 3.11 or newer
- Node.js with `npx`
- Git when baseline comparison is enabled
- Network access for the pinned tool installation unless the runner already caches the packages

GitHub-hosted Ubuntu runners satisfy these requirements. The action installs its Python package and pinned Lizard release, while npx executes the pinned jscpd release.

## Local development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" lizard==1.23.0
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m build
```

Run the CLI directly with:

```bash
PYTHONPATH=src .venv/bin/python -m ai_code_quality \
  --path . \
  --level standard \
  --require-improvement false
```

## License

[MIT](LICENSE)
