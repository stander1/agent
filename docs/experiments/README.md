# Historical Experiment Archive

This directory records AgentLite's iterative experiment history. It is retained
so reviewers can trace implementation decisions, intermediate measurements,
and the evidence that motivated later changes.

## Status Semantics

- A document named `results` records one bounded run, not a permanent product
  property.
- A `formal-regression` document records a frozen regression boundary.
- An intermediate result is superseded when a later version changes the
  relevant implementation or acceptance contract.
- Only the final competition snapshot and immutable release acceptance archive
  define the current release status.

## Current Evidence Entry Points

- `../competition/RESULTS_SNAPSHOT.md`
- `../competition/v0.5.15-delivery-guide.md`
- `../release/v0.5.15-final-release-notes.md`

The archive intentionally keeps task scope, metric definitions, and evidence
boundaries visible. Communication tokens, Provider tokens, deterministic
quality checks, and model-judged delivery quality are separate metrics and must
not be substituted for one another.
