# v5.15s Cross-Source Predecessor Binding

This mechanism acceptance closes the generic ordering defect frozen by v5.15r:
a revision proposal could be rejected because its predecessor literal was absent
from the new claim source span even though predecessor identity is owned by the
local memory graph.

The production invariant is domain independent:

- claim value, unit, and source span remain exact-source validated;
- non-supersession relation targets remain source-span validated;
- externally supplied candidate identifiers are rejected;
- a supersession target omitted from the new source is treated as untrusted and
  removed before admission;
- MemoryStore may bind only a unique active predecessor with the same semantic
  identity; unresolved bindings cannot resolve the conflict;
- every hydrated team receiver gets the model response boundary and the real
  task sequence index in its applied rewrite audit.

The verifier inherits every v5.15r gate and additionally requires one active and
one historical claim with the same semantic identity, a revision source span
that omits the historical literal, a locally bound supersession relation, and a
MemoryView that closes the active/historical pair.

External scenario and team files must be authored after the implementation
commit and must describe a domain not used while implementing this change.

```bash
export AGENTLITE_V515S_SCENARIO_FILE=/external/scenario.json
export AGENTLITE_V515S_TEAM_FILE=/external/team.json
bash experiments/v5.15s-cross-source-predecessor-binding/run_openeuler.sh
```

Provider retries are disabled for the formal holdout so failures remain visible
rather than being hidden by repeated calls.
