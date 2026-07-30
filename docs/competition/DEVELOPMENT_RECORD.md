# AgentLite Development Record

## Purpose

This document provides a concise route through the repository's complete Git
history. Detailed intermediate reports remain in `docs/experiments`, while the
current competition claims are summarized in `RESULTS_SNAPSHOT.md`.

## Development Phases

| Phase | Main engineering outcome |
|---|---|
| v0-v2 | Reproducible baselines, tokenizer-backed accounting, initial StatePool and MemoryStore |
| v3-v4 | Delivery schemas, candidate admission, output guards, retry budgets, leases, and lifecycle governance |
| v5.1-v5.11 | Typed protocol bridge, persistence, communication governance, access control, cross-task evaluation, and latency instrumentation |
| v5.12 | Native AutoGen takeover across Team, Agent, message, handoff, tool-summary, and Core boundaries |
| v5.13 | Immutable experiment binding, Studio Run identity, capability-scoped views, continuity memory, and conflict-aware facts |
| v5.14 | Review governance, semantic fidelity, typed reliability, evidence continuity, revision resolution, and final-delivery guards |
| v5.15 | Generic semantic bridge, exact provenance spans, schema validation, controlled disambiguation, package hardening, and final openEuler release |

## Evidence Model

AgentLite uses four evidence layers:

1. unit and mechanism tests for deterministic contracts;
2. controlled AutoGen benchmarks for transport and takeover behavior;
3. bounded Provider experiments for cost and delivery observations;
4. immutable openEuler release archives for installation and compatibility.

Each layer answers a different question. Historical commits preserve how the
implementation and its acceptance boundaries evolved; the final release status
is determined only by the latest applicable gate.

## Final Submission State

- version: `0.5.15`;
- license: Apache-2.0;
- final openEuler acceptance: 22 / 22;
- inherited release acceptance: 15 / 15;
- full unit suite: 602 / 602;
- wheel and sdist: independently inspected and installed;
- publication blockers: 0.

## Traceability

- Version map: `docs/versioning.md`
- Implementation map: `docs/planning/version-implementation-mapping.md`
- Experiment archive: `docs/experiments/`
- Release notes: `docs/release/v0.5.15-final-release-notes.md`
- Delivery guide: `docs/competition/v0.5.15-delivery-guide.md`
- Current results: `docs/competition/RESULTS_SNAPSHOT.md`
