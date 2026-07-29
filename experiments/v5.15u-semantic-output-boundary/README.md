# v5.15u Semantic Output Boundary

This acceptance inherits all v5.15t gates and proves that memory attribution
uses decoded model content rather than the audit-rendered `source: content`
transport surface.

The verifier requires:

- every observed attribution event to use
  `attribution_surface=decoded_model_content_v1`;
- open-candidate-only attribution with zero legacy-domain candidates;
- at least one exact open-predicate match;
- all inherited revision, typed-context, cost, retry, and delivery checks.

Scenario and team inputs must be external, authored after the implementation
commit, and use an implementation-time unseen domain. Provider retries remain
disabled.

```bash
export AGENTLITE_V515U_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515U_TEAM_FILE=/external/team.json
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15u-semantic-output-boundary/run_openeuler.sh
```
