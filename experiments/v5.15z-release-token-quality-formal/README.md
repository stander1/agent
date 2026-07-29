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
