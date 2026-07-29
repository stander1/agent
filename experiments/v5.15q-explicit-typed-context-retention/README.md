# v5.15q Explicit Typed Context Retention Acceptance

This post-commit holdout verifies the boundary that failed in the preceding
archived run: admitted typed facts existed in MemoryView, but an explicit
current/history request lost historical and related active facts while the
role view was compacted to its token budget.

The scenario and team configuration remain external JSON files authored after
the implementation and verifier commit. The production runtime and verifier
do not contain holdout domain names, predicates, values, units, or agent names.

The gate requires all v5.15p typed revision checks plus:

- every supported supersession relation kind uses the same local predecessor
  binding contract;
- the final task declares an additional typed fact beyond the revision pair;
- the final model-visible input for every configured receiver contains the
  complete active fact, historical predecessor, and additional typed fact;
- the verifier parses typed fact records from the trace instead of accepting
  output text as proof that context injection occurred;
- Provider, control, transport, field-fetch, retry, quality, and protocol
  hygiene evidence remains present.

Run on openEuler after authoring the external inputs:

```bash
export AGENTLITE_V515Q_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515Q_TEAM_FILE=/external/team.json
export OPENAI_API_KEY
export OPENAI_BASE_URL
export OPENAI_MODEL
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15q-explicit-typed-context-retention/run_openeuler.sh
```

A passing archive proves that this committed implementation retained the
preregistered typed context in one unseen revision chain. It does not establish
universal domain coverage or a Native/Observed/Managed cost-quality result.
