# v5.15z Release Token and Quality Formal Benchmark

This benchmark reruns the frozen A1-A10 and B1-B10 formal workload on the
AgentLite `v0.5.15` production source:

- Native, Observed, and Managed groups;
- identical task files, capability-based Agent profiles, model, temperature,
  turn limit, judges, and acceptance thresholds from v5.14z;
- three serial repeats, for 180 task executions in total;
- per-repeat immutable archives plus a pooled batch summary;
- explicit separation of Provider, transport, control-LLM, retry, quality,
  delivery, memory, state, and protocol evidence.

The harness may be committed after the release tag, but the verifier rejects
the run unless every path changed since `v0.5.15` is inside this experiment
directory. Production code is therefore measured exactly as released.

`start_background.sh` reads the Provider key from standard input (or a hidden
interactive prompt), keeps it out of command lines and logs, and launches the
three-repeat batch in the background.

## Offline analysis

After one or more immutable repeats have been retrieved:

- `aggregate_repeats.py` reports Native, Observed, and Managed Provider usage,
  retries, quality, delivery, memory attribution, judge usage, A/B scenario
  breakdowns, sample standard deviations, and 95% Student-t confidence
  intervals. The original pooled release gates remain unchanged.
- `analyze_fact_fidelity.py` traces open, source-bound canonical claims through
  request, receive, memory, rewrite, agent-output, and final-answer
  checkpoints. A visibility loss is diagnostic only; frozen technical findings
  remain the authority for quality failure.
- `run_memory_ablation.sh` is an explicit, standalone Provider experiment. It
  changes only `AGENTLITE_AUTOGEN_SHARED_MEMORY` between `memory-on` and
  `memory-off`, never runs from `run_repeats.sh`, and compares the variants with
  Native-controlled difference-in-differences via
  `compare_memory_ablation.py`.

These tools do not alter production runtime behavior and are safe to develop
locally while an immutable formal repeat is running elsewhere.
