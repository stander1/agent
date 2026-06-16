# Agent 协同低开销通信系统

本仓库用于迭代实现一个面向多 Agent 协作的跨框架运行时工具层，目标是在多 Agent 任务中通过结构化通信、非文本状态传递和共享记忆复用降低协作开销。

当前版本：`v3.3 memory candidate admission lite`

实验结果记录见：

- [docs/experiments/v0-v1-results.md](docs/experiments/v0-v1-results.md)
- [docs/experiments/v2-llm-travel-results.md](docs/experiments/v2-llm-travel-results.md)
- [docs/experiments/v2-longcontext-travel-results.md](docs/experiments/v2-longcontext-travel-results.md)
- [docs/experiments/v2-longcontext-quality-judge.md](docs/experiments/v2-longcontext-quality-judge.md)
- [docs/experiments/v2-security-b-results.md](docs/experiments/v2-security-b-results.md)
- [docs/experiments/v3-lite-smoke-results.md](docs/experiments/v3-lite-smoke-results.md)
- [docs/experiments/v3-full-ab-results.md](docs/experiments/v3-full-ab-results.md)
- [docs/experiments/v3.1-final-schema-results.md](docs/experiments/v3.1-final-schema-results.md)
- [docs/experiments/v3.2-reliability-guard-results.md](docs/experiments/v3.2-reliability-guard-results.md)
- [docs/experiments/v3.3-memory-admission-results.md](docs/experiments/v3.3-memory-admission-results.md)

版本切换与 GitHub 浏览方式见：[docs/versioning.md](docs/versioning.md)

版本路线与代码实现对照见：[docs/planning/version-implementation-mapping.md](docs/planning/version-implementation-mapping.md)。后续小版本必须先在该文档中校准归属主版本和对应创新方案模块。

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

## v2 LLM 实验入口

v2 开始提供可选真实 LLM evaluation harness。API key 不写入仓库，建议通过本地环境变量提供：

```powershell
$env:MIMO_API_KEY='你的本地 key'
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json
```

默认任务集为 `benchmarks/travel_task_group_a.json`，来自 `question/A.md` 的个性化旅行规划任务组。

当前 MiMo 示例配置使用开发者计划 Token Plan 的 OpenAI-compatible 地址：

```text
https://token-plan-cn.xiaomimimo.com/v1
```

`https://token-plan-cn.xiaomimimo.com/anthropic` 属于 Anthropic-compatible 接口，当前 v2 runner 暂未启用。

v2.3 将 Runtime State Pool 升级为 Hot / Warm / Cold 三层。完整 artifact content 只进入 Cold audit payload，普通 Agent 仍只读取 Prompt View。

最新 A/B 两组连续任务实验显示：

```text
A1-A10 旅行规划:
  end_to_end_collaboration_tokens 降低 91.7%
  llm_total_tokens 降低 87.2%

B1-B10 合成安全审计:
  end_to_end_collaboration_tokens 降低 90.9%
  llm_total_tokens 降低 86.7%
```

但两组 v2.3 的 MiMo 严格质量裁判都判定 `baseline_text` 的最终收束结果更完整，说明 v3 需要重点补 ClaimCard / MemoryView / Deliverable View / Reviewer 修复闭环，避免低开销压缩牺牲最终交付质量。

v3-lite 已开始补齐这条质量链路：当前实现了 Promotion View、ClaimCard、MemoryView、Alias Mapping 和 Deliverable View。完整 A/B 重跑显示：A 组旅行规划仍由 `baseline_text` 质量胜出，说明通用 Deliverable View 还不足以生成具体行程手册；B 组合成安全审计中 `runtime_lite` 质量反超 baseline，说明 Claim/Evidence/Decision Log 类任务已经能从 v3 记忆视图中受益。

v3.1 在 v3 基础上加入 Final Deliverable Schema：A10/B10 最终收束任务会注入领域化 schema，Reviewer 可触发一次短上下文修复，并记录 `deliverable_schema_complete` / `final_quality_retry_count`。正式 A/B 实验显示：

```text
A1-A10 旅行规划:
  end_to_end_collaboration_tokens 降低 88.8%
  llm_total_tokens 降低 85.1%
  schema 覆盖 16/16
  质量裁判 baseline_text 胜出 26 vs 22

B1-B10 合成安全审计:
  end_to_end_collaboration_tokens 降低 89.7%
  llm_total_tokens 降低 86.3%
  schema 覆盖 12/12
  质量裁判 runtime_lite 胜出 35 vs 22
```

结论：v3.1 已证明低开销通信和最终交付 schema 可以兼容；但 A 组仍需要在后续版本增强领域事实保真和细节充分性，而 B 组这类证据链任务已经比较适合当前 Runtime Lite 路线。

v3.2 按照创新方案中的 Output Contract Guard 路线补齐输出可靠性地基：

```text
Provider Response Guard:
  先规则恢复 provider response envelope，恢复不了才 retry。

Agent Output Contract Guard:
  提取 <CMJCC_CONTROL>，规则修复 JSON，schema 校验，默认值补齐，
  失败时短上下文 Same-Agent Format Retry，仍失败才 degraded_fallback。
```

v3.2 smoke 结果：

```text
A1 runtime_lite:
  contract_guard_checked_count: 5
  contract_schema_valid_count: 5
  schema_valid_rate: 1.0
  contract_retry_count: 0
  fallback_count: 0
```

这一步不是追求更高质量分，而是保证后续 v4 的 Retry Budget、Read Lease、GC 等机制建立在稳定输出契约之上。

v3.3 按照 TLC-Memory 创新方案补齐 Memory Candidate Admission Lite：

```text
Contract Guard control.memory_card / claim_cards
  -> MemoryCandidate / ClaimCandidate
  -> Admission Lite
  -> admitted candidates only
  -> MemoryStore / ClaimCard / MemoryView
```

A1 runtime_lite smoke 显示：

```text
memory_candidate_count: 3
memory_admitted_count: 3
memory_rejected_count: 0
memory_pending_count: 0
memory_audit_only_count: 0
admission_unresolved_slot_count: 0
claim_to_memoryview_count: 3
memory_admission_rate: 1.0
schema_valid_rate: 1.0
```

这一步确认 `memory_card` 不再被视为最终长期记忆，而是先进入候选池和准入门控；只有 admitted candidate 才更新 MemoryView。
