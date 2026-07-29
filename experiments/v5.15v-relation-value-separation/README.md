# v5.15v Relation-Value Separation

This acceptance inherits every v5.15u gate and verifies that relation wording
cannot become an active claim value. The mechanism uses source spans, open
predicate identity, value types, local adjacency, and relation structure. It
does not use scenario, role, unit, or domain vocabularies.

The verifier requires:

- decoded model content for memory attribution;
- at least one exact open-predicate attribution match;
- zero legacy-domain attribution candidates;
- zero active relation/value conflations;
- all inherited revision, typed-context, cost, retry, and delivery checks.

Scenario and team inputs must be external, authored after the implementation
commit, and use an implementation-time unseen domain. Provider retries remain
disabled.

```bash
export AGENTLITE_V515V_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515V_TEAM_FILE=/external/team.json
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15v-relation-value-separation/run_openeuler.sh
```
