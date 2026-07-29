# v5.15x Release Candidate Freeze

This gate builds and verifies AgentLite `0.5.15rc1` on openEuler. It combines
the independent installed-package gate, the complete source release gate, and
the wheel/sdist artifact builder under one immutable evidence archive.

Run from a clean tracked checkout:

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515X_RUN_ROOT
unset AGENTLITE_V515X_EXPORT_BASE
export AGENTLITE_V515X_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15x-release-candidate-freeze/run_openeuler.sh
```

No Provider credential is required. A technically passing RC remains marked
as not ready for public open-source publication until an explicit repository
license is selected by the project owner.
