# AI Code Quality

Profiled repository-wide complexity, duplication, security, lint, and typo gates for multi-language projects.

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
```

The action scans the complete repository, not only changed files. It combines jscpd, Lizard, Semgrep, yamllint, markdownlint, and typos behind one monotonic profile and one bounded report.

By default, every enabled debt or finding metric must independently improve by at least 2% against the automatically resolved baseline. A metric already at zero must remain at zero. Semgrep `ERROR` findings block immediately and are never grandfathered. Use `require-improvement: "false"` when you want only the selected profile's absolute limits.

`v1` pins jscpd `5.0.14`, Lizard `1.23.0`, Semgrep `1.172.0`, yamllint `1.38.0`, markdownlint-cli `0.49.1`, and typos `1.48.0` for reproducible measurements. Semgrep rules and lint policies are action-owned and profile-versioned, so identical commits do not depend on mutable registry defaults.

## Quality levels

| Level | Duplication | CCN | Function length | Arguments | Semgrep | YAML | Markdown | Typos |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| `none` | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled |
| `minimal` | 20% | 30 | 200 | 10 | Disabled | Disabled | Disabled | Enabled |
| `basic` | 15% | 20 | 150 | 9 | Basic | Relaxed | Core | Enabled |
| `standard` | 10% | 15 | 100 | 7 | Standard | Standard | Standard | Enabled |
| `strict` | 0% | 10 | 75 | 6 | Strict | 120 columns | 120 columns | Enabled |
| `hardened` | 0% | 8 | 60 | 5 | Hardened | 100 columns | 100 columns | Enabled |
| `maximum` | 0% | 5 | 40 | 4 | Maximum | 80 columns | 80 columns | Enabled |

Limits are inclusive. A function exactly at its CCN, length, or argument limit passes. A duplication percentage exactly at a nonzero limit also passes. At a 0% duplication limit, any detected duplicated lines fail even if jscpd rounds the displayed percentage to `0.00%`.

The profiles are monotonic: stronger profiles never loosen or disable checks from weaker profiles. Mutation testing is not part of v1.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `level` | `standard` | One of `none`, `minimal`, `basic`, `standard`, `strict`, `hardened`, or `maximum`. |
| `require-improvement` | `2` | Absolute, report-only, maintenance, or percentage-improvement enforcement. |
| `path` | `.` | Repository or subdirectory to analyze. |
| `baseline-ref` | empty | Explicit Git ref for baseline comparisons. |
| `repair-limit` | `15` | Maximum findings placed in the bounded AI repair batch. |
| `annotation-limit` | `40` | Maximum source annotations written to GitHub. |

### `require-improvement`

| Value | Behavior |
| --- | --- |
| `"false"` | Enforce the selected profile's absolute limits immediately. No baseline is used. |
| `"-1"` | Report quality findings without failing the action. Scanner or configuration errors still fail. |
| `"0"` | Permit existing ratcheted debt but require every enabled metric not to regress. Semgrep errors still block. |
| Positive number | Require every enabled ratcheted metric to improve by at least that percentage. Semgrep errors still block. |

Improvement is evaluated independently for:

- Duplicated-line percentage
- Complexity debt
- Function-length debt
- Argument-count debt
- Non-error Semgrep finding count
- yamllint finding count
- markdownlint finding count
- Typo count

The three per-function debts are:

```text
complexity debt = sum(max(0, function CCN - profile CCN limit))
function-length debt = sum(max(0, function length - profile length limit))
argument debt = sum(max(0, function parameters - profile parameter limit))
```

For an improvement percentage `p`, the allowed current duplication is `baseline duplication * (1 - p/100)`. Integer debts and finding counts use the same calculation rounded down to a whole number. A clean baseline remains clean. Current Semgrep `ERROR` findings always fail in blocking modes, regardless of baseline findings.

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
| `function-length-debt` | Total function-length debt for the selected profile. |
| `argument-debt` | Total argument-count debt for the selected profile. |
| `semgrep-findings` | Current non-error Semgrep finding count. |
| `yamllint-findings` | Current yamllint finding count. |
| `markdownlint-findings` | Current markdownlint finding count. |
| `typo-findings` | Current typo finding count. |
| `report-path` | Absolute path to the complete JSON report. |
| `fix-context-path` | Absolute path to the bounded AI repair context. |
| `baseline-sha` | Resolved baseline commit when comparison mode is active. |

## Reports for humans and coding agents

The action deliberately avoids dumping every scanner result into the job log.

### GitHub job summary

The bounded summary contains:

- Overall result, profile, and enforcement mode
- Current and allowed duplication
- Current and allowed complexity, length, argument, security, lint, and typo debt
- Constraints that already pass and should not regress
- Near-limit passing functions
- The highest-priority repair batch
- Paths to complete machine-readable reports

### Source annotations

Failing functions are annotated with the function name, line range, observed metric, and allowed limit. Semgrep, YAML, Markdown, and typo findings include their rule, message, and source coordinates. Duplicate clone fragments include their source ranges and stable family identifier. The number of annotations is bounded by `annotation-limit`.

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

The schema-v2 complete report contains every function measurement, duplicate family, scanner finding, policy and tool version, check status, threshold, baseline measurement, and enforcement setting. It is not dumped into the normal log.

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

### Function metrics

One Lizard CSV scan supplies cyclomatic complexity, function length, and parameter count. Each dimension is evaluated as a separate debt so improvement in one cannot hide regression in another.

### Security and correctness

Semgrep uses action-owned rules selected by the profile. `ERROR` findings are immediate blockers in absolute and improvement modes. Lower-severity findings are ratcheted by count. Scanner parse errors, rule errors, timeouts, missing binaries, and malformed output fail closed.

### YAML, Markdown, and typos

yamllint and markdownlint use action-owned monotonic policies. Repositories can still use normal ignore files, including `.markdownlintignore`. typos honors its repository configuration and dictionaries. Finding counts are ratcheted independently; stronger YAML and Markdown profiles tighten line-length and structural policy.

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
- Git when baseline comparison is enabled
- Network access for pinned package and binary installation unless the runner already caches them

The action provisions its own Node.js 22 runtime and npm through `actions/setup-node@v7`. GitHub-hosted Ubuntu, macOS, and Windows x64 runners satisfy the remaining requirements. The pinned typos installer also supports Linux ARM64 and macOS ARM64. Downloads are size-bounded and SHA-256 verified before extraction.

## Local development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" \
  lizard==1.23.0 semgrep==1.172.0 yamllint==1.38.0
npm install --prefix /tmp/ai-code-quality-node markdownlint-cli@0.49.1
PYTHONPATH=src .venv/bin/python -m ai_code_quality.install_typos /tmp/ai-code-quality-bin
PATH="/tmp/ai-code-quality-node/node_modules/.bin:/tmp/ai-code-quality-bin:$PATH" \
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

## Profile runtime benchmarks

The scheduled and manually dispatched `Profile runtime benchmarks` workflow measures the
end-to-end runtime users experience when invoking this composite action. A monotonic timer starts
immediately before `uses: ./action` and stops when the action returns. It includes the action's
isolated tool installation, scanners, policy evaluation, and report generation. It excludes the
benchmark workflow's repository checkouts and Python setup. GitHub-hosted runners start without a
benchmark-owned shared tool cache, so these are cold action-invocation measurements.

The versioned corpus covers Python, JavaScript, TypeScript, Java, C, C++, C#, Go, Rust, Ruby, PHP,
and Swift. Each language contributes two fixed, established repositories plus one rotating
repository selected from a curated candidate pool. The rotating choice is derived from the GitHub
run ID using SHA-256, making it deterministic for the run and stable across rerun attempts. The
preparation job resolves and records the exact default-branch commit for every selected repository
before any scans begin.

Every repository/profile pair is an independent matrix cell:

```text
12 languages x 3 repositories x 7 profiles = 252 jobs
```

The matrix uses `fail-fast: false`, so one repository does not cancel unrelated measurements.
GitHub account concurrency limits can still queue cells; independence does not guarantee that all
252 runners execute simultaneously. The run consumes 252 fresh Linux job runtimes, so cold setup
and action installation are intentionally part of its billable runner cost. Each cell uploads one
identity-bound JSON result plus the complete schema-v2 action report. The result binds the report
bytes by SHA-256 and records the run ID and attempt, language, repository, pinned commit, profile,
runner, action commit, outcome, token count, and elapsed seconds. Fan-in independently recomputes the
report hash and validates its exact fields, field types, profile enablement, check outcomes, and
verdict consistency. A quality-policy failure remains comparable only when all evidence agrees.
Missing reports, setup failures, malformed records, missing cells, duplicate identities within an
attempt, and any infrastructure-failed cell block badge publication.

After all cells finish, one aggregation job validates the complete 252-cell ledger. It calculates
p50 and p95 with the deterministic nearest-rank method: sort durations and select rank
`ceil(percentile * sample_count)`. It also normalizes each token-bearing runtime to seconds per one
million jscpd tokens using `duration_seconds * 1,000,000 / total_tokens`. The token count comes from
the same jscpd report produced inside the timed action, so normalization neither reruns nor warms the
scanner. The `none` profile deliberately skips jscpd, so its 36 cells remain in raw runtime
percentiles but are excluded from the 216-sample normalized percentiles. The workflow writes Shields
endpoint documents for the complete corpus and for every applicable profile:

```text
.github/badges/runtime-p50.json
.github/badges/runtime-p95.json
.github/badges/runtime-per-million-tokens-p50.json
.github/badges/runtime-per-million-tokens-p95.json
.github/badges/runtime-none-p50.json
.github/badges/runtime-none-p95.json
...
.github/badges/runtime-maximum-p50.json
.github/badges/runtime-maximum-p95.json
.github/badges/runtime-maximum-per-million-tokens-p50.json
.github/badges/runtime-maximum-per-million-tokens-p95.json
```

Aggregation runs read-only and hands only validated badge artifacts to a minimal publication job
with repository write permission. Partial workflow reruns reuse the pinned selection, download cell
evidence from every attempt in the same run, and select the newest valid attempt for each cell;
duplicate records within one attempt and records from a future attempt are rejected. Publication
records the `(run_id, run_attempt)` lineage beside the badges and rechecks it against `main` after
every remote refresh, so rerunning an older workflow cannot roll back newer results. Pull requests
cannot publish the benchmark, and concurrent benchmark runs are serialized. The result ledger and
generated badges are also retained as workflow artifacts. Corpus membership is maintained in
`.github/benchmarks/repositories.json`; changes to it are ordinary reviewed repository changes.

## License

[MIT](LICENSE)
