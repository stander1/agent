# AgentLite

AgentLite is a cross-framework runtime layer for low-overhead multi-agent
collaboration. It adds structured state transport, governed shared memory,
cost-aware message rewriting, reliability guards, and observable AutoGen
takeover without requiring user programs to import AgentLite.

Current competition release: `v0.5.15`

- License: [Apache-2.0](LICENSE)
- Python: `>=3.11`
- Runtime package: `multi-agent-collaboration-runtime`
- Primary validation platform: openEuler

## Competition Snapshot

The current release surface is backed by immutable local and openEuler evidence.

| Validation item | Verified result |
|---|---:|
| Final openEuler release checks | 22 / 22 passed |
| Inherited release checks | 15 / 15 passed |
| Full Python unit suite | 602 / 602 passed |
| Wheel inspection | passed |
| Isolated sdist install, import, and CLI | passed |
| Publication blockers | 0 |

The controlled deterministic AutoGen Team benchmark verifies transparent
takeover and communication reduction while preserving its declared delivery
contract:

| Metric | Native AutoGen | AgentLite managed | Change |
|---|---:|---:|---:|
| First Team input tokens | 2,071 | 1,045 | -49.54% |
| Team broadcast transport tokens | 6,213 | 900 | -85.51% |
| Agent input tokens | 3,315 | 919 | -72.28% |
| Deterministic delivery score | 12 / 12 | 12 / 12 | no loss |

These token figures are communication-layer measurements from the frozen
deterministic benchmark. They are not presented as Provider billing-token
reductions or as a universal cross-domain quality claim. Exact scope and
reproduction links are recorded in
[the competition results snapshot](docs/competition/RESULTS_SNAPSHOT.md).

## What AgentLite Provides

- **Structured state transport**: StatePool references and receiver-specific
  Prompt Views replace repeated full-text transport where the cost and contract
  guards allow it.
- **Governed memory**: typed candidates, admission, provenance, conflict
  resolution, revision lineage, and task-scoped MemoryViews.
- **AutoGen takeover**: Team, Agent, Core, Studio Run binding, and final-delivery
  boundaries are instrumented through the managed launcher.
- **Semantic fidelity**: open canonical claims, exact source spans, schema
  registry validation, typed review events, and controlled disambiguation.
- **Reliability and observability**: safe fallback, retry accounting, immutable
  experiment binding, CLI reports, and a local workflow monitor.
- **Release engineering**: Apache-2.0 metadata, wheel and sdist inspection,
  isolated installation, openEuler regression, and SHA256 evidence archives.

## Install

From the wheel:

```bash
python -m pip install multi_agent_collaboration_runtime-0.5.15-py3-none-any.whl
agentlite version
agentlite doctor --framework autogen --json
```

For development and AutoGen integration:

```bash
python -m pip install -e ".[autogen]"
```

Run an existing AutoGen program under AgentLite:

```bash
agentlite autogen -- python your_autogen_app.py
```

## Verify

Run the final no-Provider release gate on openEuler:

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

The release is valid only when every acceptance check passes, the installed
wheel and sdist resolve outside the source checkout, publication blockers are
empty, and artifact hashes match the recorded values.

## Documentation

- [Competition delivery guide](docs/competition/v0.5.15-delivery-guide.md)
- [Validated results snapshot](docs/competition/RESULTS_SNAPSHOT.md)
- [Development record](docs/competition/DEVELOPMENT_RECORD.md)
- [Final release notes](docs/release/v0.5.15-final-release-notes.md)
- [Version and branch map](docs/versioning.md)
- [Historical experiment archive](docs/experiments/README.md)

Historical experiments and intermediate results remain available for
development-process traceability. Current release claims are defined by the
competition snapshot and the final immutable acceptance evidence.

## Security

Provider credentials are runtime-only inputs. They must not be committed,
printed in commands, copied into logs, or included in experiment archives.
Release compatibility checks do not require Provider credentials.
