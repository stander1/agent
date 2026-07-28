# v5.15p Closed Revision Measurement Acceptance

This post-commit holdout closes the gap left by a surface-only successful
answer. It verifies the admitted memory state and retrieval ledger directly.

The scenario and team configuration are external JSON files authored after the
implementation and verifier commit. The production runtime and verifier do not
contain the holdout domain, predicate names, values, units, or agent names.

The acceptance gate requires:

- the base integrated AutoGen semantic preflight to pass;
- the closed nested-relation contract and evidence-bound numeric measurement
  normalization regressions to pass;
- a typed active value and typed superseded value under one semantic identity;
- the active claim's explicit revision relation to target the historical claim;
- one MemoryView to expose the new claim as active and the former claim as
  historical;
- the final task to request current and former values without restating either;
- historical expansion to increment both memory field-fetch calls and tokens;
- complete Provider, control, transport, retry, quality, and protocol hygiene
  evidence to remain present.

Run on openEuler after authoring the external inputs:

```bash
export AGENTLITE_V515P_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515P_TEAM_FILE=/external/team.json
export OPENAI_API_KEY
export OPENAI_BASE_URL
export OPENAI_MODEL
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15p-closed-revision-measurement-acceptance/run_openeuler.sh
```

A passing archive proves this committed implementation handled one unseen
revision chain end to end. It is not a claim of universal domain coverage or a
formal Native/Observed/Managed cost-quality result.