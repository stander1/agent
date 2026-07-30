# AgentLite v0.5.15 Validated Results Snapshot

## Release Readiness

The final openEuler release evidence for `v0.5.15` records:

| Check group | Result |
|---|---:|
| Final installed-sdist acceptance | 22 / 22 passed |
| Inherited release acceptance | 15 / 15 passed |
| Full Python unit suite | 602 / 602 passed |
| Focused final-release tests | 20 / 20 passed |
| Wheel inspection | passed |
| sdist inspection | passed |
| Isolated sdist installation and import | passed |
| Installed CLI version identity | passed |
| Apache-2.0 source/package identity | passed |
| Publication blockers | 0 |

The acceptance report records matching project and runtime version `0.5.15`,
`technical_release_ready=true`, `open_source_publication_ready=true`, and
`installed_sdist_verified=true`.

Reproduction entry point:

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

## Controlled AutoGen Communication Benchmark

The frozen `v5.12x` benchmark runs the same deterministic AutoGen Team program
in Native and AgentLite-managed modes. The user program imports AutoGen but
does not import AgentLite.

| Metric | Native | Managed | Managed change |
|---|---:|---:|---:|
| First Team input tokens | 2,071 | 1,045 | -49.54% |
| Team broadcast transport tokens | 6,213 | 900 | -85.51% |
| Agent input tokens | 3,315 | 919 | -72.28% |
| Deterministic delivery score | 12 / 12 | 12 / 12 | 0 |

The run passed 20 / 20 benchmark checks. Managed execution recorded one Team
rewrite and three Agent-input rewrites with zero fallback on the benchmark's
happy path.

Detailed evidence:

- `docs/experiments/v5.12x-autogen-team-benchmark-results.md`
- `examples/run_autogen_team_benchmark.py`

## Metric Boundaries

The communication benchmark measures tokenizer-backed message and Prompt View
content at the runtime boundary. It does not equate those values with Provider
billing tokens.

The deterministic 12 / 12 score validates the benchmark's declared delivery
contract. It is not a claim that every model, prompt, framework, or natural
language domain has identical quality.

All current release claims remain bounded by the checked-in experiment
definition and the retrieved immutable evidence.
