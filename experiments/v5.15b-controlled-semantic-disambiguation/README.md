# v5.15b Controlled Semantic Disambiguation Acceptance

This gate verifies the optional LLM proposal path added after the deterministic
v5.15a semantic bridge. It does not run the frozen Question A/B scenarios and
does not treat model output as authoritative.

The mechanism gate uses scripted responses to verify:

- deterministic extraction runs before the control path;
- exact evidence quotes are resolved locally into `SourceSpan`;
- malformed, fabricated, repeated, cross-task, and over-budget proposals fail
  closed;
- accepted proposals still pass semantic validation, schema resolution,
  conflict handling, and normal Memory Admission;
- control calls, retries, latency, and tokens are included in end-to-end cost;
- AutoGen integration is disabled by default and requires explicit opt-in.

An external holdout JSON must be authored after the implementation commit. Its
contents are copied into the immutable archive and are never imported by
production code.

```bash
export AGENTLITE_V515B_HOLDOUT_FILE=/path/to/external-v515b-holdout.json
export AGENTLITE_V515B_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15b-controlled-semantic-disambiguation/run_openeuler.sh
```

The holdout schema is `agentlite.v515b.holdout.v1`. It requires at least three
positive cases from unrelated domains and two negative evidence cases. Each
positive case contains a source, a scripted proposal, and expected canonical
fields. Negative cases use `repeated_quote` or `missing_quote`.

Passing this gate proves the bounded proposal and validation mechanics. It does
not prove universal language understanding; that requires a separate real
Provider preflight with quality and full-cost evidence.
