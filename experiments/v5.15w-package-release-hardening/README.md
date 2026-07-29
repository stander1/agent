# v5.15w Package Release Hardening

This gate proves that the current AgentLite implementation can be built and
installed as a wheel on openEuler and that the installed AutoGen takeover
honors both valid runtime outcomes:

- a cost-reducing internal rewrite; or
- an explicit `token_not_reduced` fallback with no message mutation.

It also checks that the v5.15 semantic bridge modules are present in the wheel,
the source and installed versions match, the complete release gate passes, and
the evidence archive verifies with SHA256.

Run from a clean tracked checkout:

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515W_RUN_ROOT
unset AGENTLITE_V515W_EXPORT_BASE
export AGENTLITE_V515W_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15w-package-release-hardening/run_openeuler.sh
```

No Provider credential is required.
