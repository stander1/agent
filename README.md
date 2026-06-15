# Agent 协同低开销通信系统

本仓库用于迭代实现一个面向多 Agent 协作的跨框架运行时工具层，目标是在多 Agent 任务中通过结构化通信、非文本状态传递和共享记忆复用降低协作开销。

当前版本：`v1 runtime lite`

实验结果记录见：[docs/experiments/v0-v1-results.md](docs/experiments/v0-v1-results.md)

## v0/v1 目标

v0 是评测地基，先建立稳定、可复现的纯文本基线和指标系统。
v1 在此基础上加入三线 Lite 闭环：

- 运行确定性的多 Agent baseline 任务。
- 统计文本通信、prompt、延迟和预留的 state/memory 字段。
- 使用 tokenizer-backed `TokenCounter` 记录 token 与 tokenizer 元数据。
- 导出 `trace.jsonl`、`metrics.csv`、`metrics.json`、`summary.json`。
- 在 `runtime_lite` 模式下使用 SHP-lite、StatePool-lite、MemoryStore-lite。
- 将上游输出写入 file-backed StatePool，并通过 state_refs 渲染 Prompt View。
- 将 Writer/Reviewer 的阶段结论写入 MemoryStore，并在后续连续任务中检索复用。

## v0 快速启动

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v1_runtime_lite.py --rounds 10 --mode both
```

`--mode both` 会运行：

```text
baseline_text
runtime_lite
```

输出目录默认位于 `runs/`，其中 `state_payloads/` 保存 v1 的结构化状态 payload。

## v1 Smoke Result

最近一次 10 轮双模式 benchmark：

```text
baseline_text:
  direct_text_tokens: 159140
  prompt_view_tokens: 131260
  end_to_end_collaboration_tokens: 290400

runtime_lite:
  direct_text_tokens: 52939
  prompt_view_tokens: 104773
  retrieved_memory_tokens: 15644
  end_to_end_collaboration_tokens: 173356
```

相对 baseline：

```text
direct_text_tokens 降低约 66.7%
end_to_end_collaboration_tokens 降低约 40.3%
```

## Token 统计

v0 要求使用 `tiktoken` 或 `transformers` 进行 token 统计，不会静默使用字符估算。

只有显式传入以下参数时才允许估算：

```powershell
--allow-estimated-tokens
```

估算结果会在指标中标记：

```text
token_count_method=estimated
```

正常情况下应看到类似：

```text
token_count_method=compatible
tokenizer_name=tiktoken:cl100k_base
```
