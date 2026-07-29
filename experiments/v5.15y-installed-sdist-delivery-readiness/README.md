# v5.15y Final Installed sdist and Delivery Readiness

This gate builds AgentLite `0.5.15` on openEuler and extends the inherited
artifact checks with final-version identity, isolated sdist installation,
import-origin, installed CLI, package discovery metadata, and current
competition delivery-guide checks.

Run from a clean tracked checkout:

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515Y_RUN_ROOT
unset AGENTLITE_V515Y_EXPORT_BASE
export AGENTLITE_V515Y_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

No Provider credential is required. The final report verifies Apache-2.0 in
the repository, package metadata, wheel, and sdist, requires a non-prerelease
version, and requires all publication blockers to be cleared.
