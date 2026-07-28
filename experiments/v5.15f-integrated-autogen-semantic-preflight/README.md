# v5.15f Integrated AutoGen Semantic Preflight

This gate moves the generic semantic bridge from isolated Provider calls into
an actual AutoGen collaboration chain.

Both the task sequence and team configuration must be external JSON files
authored after, and bound to, the implementation commit. Agent names and
capabilities are supplied by that external configuration; the production
runtime does not require Planner, Writer, or Reviewer roles.

The workflow deliberately resets native AutoGen team state between tasks while
reusing the same team instance and AgentLite memory scope. This makes shared
memory retrieval and injection observable without treating an accumulated
native chat transcript as proof of continuity.

The archive contains:

- every business-agent output and main Provider usage row;
- the AgentLite trace, state, memory, and session report;
- semantic control call, acceptance, and Token metrics;
- task-chain grounding checks;
- a disjoint Token ledger for team Provider calls, control Provider calls, and
  collaboration transport excluding the already-counted control calls.

Run on openEuler:

```bash
export AGENTLITE_V515F_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515F_TEAM_FILE=/external/team.json
export OPENAI_API_KEY
export OPENAI_BASE_URL
export OPENAI_MODEL
export OPENAI_MAX_RETRIES=0
bash experiments/v5.15f-integrated-autogen-semantic-preflight/run_openeuler.sh
```

Passing v5.15f allows a comparative Native/Observed/Managed preflight. It does
not establish formal Token savings or quality noninferiority by itself.
