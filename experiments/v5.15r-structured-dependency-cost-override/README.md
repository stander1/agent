# v5.15r Structured Dependency Cost Override Acceptance

The archived v5.15q holdout retained the complete typed revision pair and
related fact in its compact role view, but the final task still failed. A
zero-retry Provider error made the control semantic-dependency decision return
`required=false`; every final receiver then rejected the longer evidence view
at the token-reduction gate and saw only the cleared native transcript.

This stage repairs that generic boundary without adding a benchmark or domain
rule:

- explicit current/history intent uses the existing structured temporal parser
  and produces an auditable zero-call dependency decision;
- the dependency reason propagates to every receiver in the task sequence;
- a nonreducing rewrite may cross the cost gate only when typed memory was
  retained and that structured continuity reason is present;
- the model-facing rewrite declares context records as evidence rather than an
  output format;
- trace evidence records the typed facts and response boundary actually visible
  only after a rewrite was applied.

The verifier extends all v5.15q checks. It additionally requires exactly one
zero-cost structured dependency event for the final task, applied rewrites for
every configured receiver, a continuity override for every expanded final
rewrite, no cost-guard passthrough, and a model response boundary for every
receiver.

Run on openEuler with external inputs authored after the implementation commit:

```bash
export AGENTLITE_V515R_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515R_TEAM_FILE=/external/team.json
export OPENAI_API_KEY
export OPENAI_BASE_URL
export OPENAI_MODEL
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15r-structured-dependency-cost-override/run_openeuler.sh
```

A pass demonstrates this mechanism in one post-commit unseen revision chain.
It is not evidence of universal domain coverage or formal Native/Managed
cost-quality noninferiority.