# v5.15t Open Attribution Isolation

This mechanism acceptance proves that useful/wrong memory evidence no longer depends on the frozen legacy domain extractor.

The verifier inherits every v5.15s gate and additionally requires:

- at least one AutoGen memory-attribution event and evidence row;
- every event and row to use `ccf_v3_open_candidate_evidence`;
- zero legacy-domain candidates in attribution;
- at least one exact open-predicate evidence match;
- the existing cross-source revision, typed context, cost, retry, and delivery gates to remain satisfied.

External scenario and team files must be authored after the implementation commit and must use a domain that was not consulted while implementing the change. Provider retries remain disabled so malformed or unavailable responses stay visible.

```bash
export AGENTLITE_V515T_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515T_TEAM_FILE=/external/team.json
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15t-open-attribution-isolation/run_openeuler.sh
```