# Multi-Agent Collaboration Runtime

This repository contains a cross-framework runtime prototype for low-overhead multi-agent collaboration.

The v0 milestone is a benchmark foundation:

- Runs deterministic multi-agent baseline tasks.
- Records text communication, prompt, latency, and placeholder state/memory fields.
- Uses a tokenizer-backed `TokenCounter` with tokenizer metadata.
- Exports `trace.jsonl`, `metrics.csv`, and `metrics.json`.

## v0 Quick Start

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v0_baseline.py --rounds 10 --mode both
```

Outputs are written to `runs/`.

v0 requires `tiktoken` or `transformers` for token counting. It does not silently use local character estimates. A CJK-aware estimate is available only when explicitly enabled with `--allow-estimated-tokens`, and the metrics will mark `token_count_method=estimated`.
