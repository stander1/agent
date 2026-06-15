# Agent 协同低开销通信系统

本仓库用于迭代实现一个面向多 Agent 协作的跨框架运行时工具层，目标是在多 Agent 任务中通过结构化通信、非文本状态传递和共享记忆复用降低协作开销。

当前版本：`v0 baseline runtime scaffold`

## v0 目标

v0 是评测地基，先建立稳定、可复现的纯文本基线和指标系统：

- 运行确定性的多 Agent baseline 任务。
- 统计文本通信、prompt、延迟和预留的 state/memory 字段。
- 使用 tokenizer-backed `TokenCounter` 记录 token 与 tokenizer 元数据。
- 导出 `trace.jsonl`、`metrics.csv`、`metrics.json`、`summary.json`。

## v0 快速启动

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v0_baseline.py --rounds 10 --mode both
```

输出目录默认位于 `runs/`。

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

