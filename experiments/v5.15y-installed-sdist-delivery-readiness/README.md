# v5.15y Installed sdist and Delivery Readiness

This gate builds AgentLite `0.5.15rc2` on openEuler and extends the inherited
RC checks with isolated sdist installation, import-origin, installed CLI,
package discovery metadata, and current competition delivery-guide checks.

Run from a clean tracked checkout:

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515Y_RUN_ROOT
unset AGENTLITE_V515Y_EXPORT_BASE
export AGENTLITE_V515Y_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

No Provider credential is required. Technical readiness remains separate from
the explicit repository-license decision required for public publication.
