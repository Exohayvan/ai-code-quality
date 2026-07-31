# AI Code Quality

AI Code Quality is a planned multi-language GitHub Action for checking the quality of an entire repository with one configurable quality profile.

> [!NOTE]
> The action is currently being designed. This README records the decisions confirmed so far and does not claim that the action is implemented yet.

## Intended usage

The action is intended to be usable as:

```yaml
- uses: exohayvan/ai-code-quality@v1
  with:
    level: standard
    require-improvement: "2"
```

Checks apply to the entire repository rather than only changed files. Repositories containing multiple supported languages will be analyzed as multi-language repositories.

## Quality levels

The supported levels will be:

```text
none
minimal
basic
standard
strict
hardened
maximum
```

Levels are cumulative. Higher levels retain checks from lower levels, tighten their limits where appropriate, and may activate additional checks.

| Level | Purpose | Runtime intent |
| --- | --- | --- |
| `none` | Disable quality checks | Essentially instant |
| `minimal` | Apply forgiving limits | Seconds |
| `basic` | Catch obvious quality problems | Fast |
| `standard` | Provide practical production expectations | Normal pull-request check |
| `strict` | Apply harsh limits using fast checks | Still appropriate for every pull request |
| `hardened` | Add deeper and substantially more expensive analysis | A large runtime increase is acceptable |
| `maximum` | Run the strongest available analysis | Runtime is a secondary concern |

`strict` is intentionally harsh without being slow. Runtime-heavy checks belong in `hardened`, while `maximum` may eventually include checks such as exhaustive mutation testing.

## Confirmed initial checks

The first version will focus on two checks:

- Duplication detection with [jscpd](https://github.com/kucherenko/jscpd)
- Cyclomatic complexity analysis with [Lizard](https://github.com/terryyin/lizard)

### Thresholds

| Level | Maximum duplicated lines | Maximum function CCN |
| --- | ---: | ---: |
| `none` | Disabled | Disabled |
| `minimal` | 20% | 30 |
| `basic` | 15% | 20 |
| `standard` | 10% | 15 |
| `strict` | 0% | 10 |
| `hardened` | 0% | 8 |
| `maximum` | 0% | 5 |

Limits are inclusive. For example, `standard` permits exactly 10% duplicated lines and a function with a cyclomatic complexity number of 15, but it rejects values above those limits.

### Duplication measurement

jscpd will use the same detection sensitivity for every active level so results remain comparable:

- Minimum duplicate size: 5 lines
- Minimum duplicate size: 50 tokens
- Comments are ignored
- `.gitignore` is respected
- The entire repository is scanned
- Common dependency, generated-output, build-output, and coverage directories are excluded by default

The selected level changes the permitted duplicated-line percentage, not the sensitivity of clone detection.

### Complexity measurement

The initial Lizard integration will enforce cyclomatic complexity per function. Function length, parameter count, file length, token count, and average file complexity are not part of the initial complexity check.

For improvement comparisons, complexity debt is measured as the sum of the amount by which each function exceeds the selected level's CCN limit:

```text
complexity debt = sum(max(0, function CCN - profile CCN limit))
```

## Improvement requirement

The `require-improvement` setting controls how check results are enforced:

| Value | Behavior |
| --- | --- |
| `false` | Do not use a baseline. The current repository must immediately satisfy the selected level's absolute limits. |
| `-1` | Report results without failing for quality findings. Tool or infrastructure failures may still fail the action. |
| `0` | Quality must not regress from the comparison baseline. |
| Positive number | Quality debt must improve by at least that percentage from the comparison baseline. |

For example:

```yaml
require-improvement: "2"
```

requires at least a 2% improvement. Duplication is compared using duplicated-line percentage, while complexity is compared using complexity debt.

The exact source-selection rules for the comparison baseline have not been finalized yet.

## Design principles

- Analyze the complete repository.
- Support repositories containing multiple languages.
- Keep checks deterministic and reproducible.
- Keep quality levels cumulative.
- Separate strictness from runtime cost.
- Keep `strict` harsh enough for demanding repositories while remaining practical on every pull request.
- Reserve expensive analysis for `hardened` and exhaustive analysis for `maximum`.
- Clearly report which checks ran, were skipped, or were not applicable.

## Not decided yet

The following details remain open and should not be treated as part of the contract yet:

- The default level and `require-improvement` value
- Exact baseline selection for pull requests, pushes, and manual runs
- The action's implementation and packaging architecture
- Final default exclusion patterns
- Report, annotation, JSON, and SARIF formats
- Additional checks beyond jscpd and Lizard
- Language-specific mutation-testing behavior
