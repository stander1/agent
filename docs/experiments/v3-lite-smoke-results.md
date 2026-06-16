# v3-lite：记忆复用与 Deliverable View 初步验证

## 版本定位

v3-lite 的目标是补齐 v2.3 暴露出的质量问题：

```text
v2.3 已经能显著降低通信 token，
但 A10/B10 最终收束任务容易输出过短摘要，
最终交付质量不稳定。
```

因此 v3-lite 先实现比赛可演示的最小记忆闭环：

- State-to-Memory Promotion View；
- Claim Card；
- Schema Registry 简化版；
- Alias Mapping Dict；
- MemoryView；
- Prompt View / Audit View / Deliverable View；
- v3 指标统计。

暂未实现：

- 真实向量库 / FAISS / Chroma；
- Context-Pruned Retry-lite；
- Reviewer 触发 Writer 自动修复；
- 完整 Memory lifecycle / CSCC。

这些能力进入 v3 后续迭代或 v4。

## 实现摘要

### MemoryStore v3-lite

`MemoryStoreLite` 从普通摘要存储升级为：

```text
Runtime State
  -> Promotion View
  -> Claim Card
  -> MemoryView
  -> Prompt / Audit / Deliverable View
```

每次 Writer / Reviewer / MemoryManager 写入记忆时，系统会：

1. 根据 task group、agent 和 slot_hint 做静态 slot 对齐；
2. 生成 PromotionView，记录 source_state_ids 和 evidence_refs；
3. 生成 ClaimCard；
4. 合并或创建 MemoryView；
5. 返回 MemoryRef，供后续 SHP-lite 消息携带。

### Deliverable View

针对 A10/B10 这类最终收束任务，runtime 会额外生成 `Deliverable View`：

```text
普通中间任务:
  Memory Prompt View + State Prompt View

最终交付任务:
  Memory Prompt View + State Prompt View + Deliverable View
```

Deliverable View 不展开完整 raw content，但会把最终交付所需的 MemoryView 摘要、slot、关键 claim 组织成更适合 Writer/Reviewer/MemoryManager 使用的上下文。

### LLM Agent 指令

最终任务增加约束：

```text
当前任务是最终收束任务：
必须输出可直接交付的完整结果，
不要只输出方法论摘要。
```

## 新增指标

v3-lite 新增以下指标：

| 指标 | 含义 |
| --- | --- |
| memory_write_count | 写入记忆数量 |
| claim_card_count | ClaimCard 数量 |
| memory_view_count | MemoryView 数量 |
| promotion_view_count | PromotionView 数量 |
| audit_view_expansion_count | Audit View 展开次数 |
| unresolved_slot_count | slot 未解析次数 |
| alias_mapping_hit_count | alias 映射命中次数 |
| vector_retrieval_count | 向量检索次数，v3-lite 仍为 0 |
| retrieval_backend | 当前检索后端 |

## Smoke：A1-A2

运行目录：`runs/v3-smoke-a1-a2-mimo25`

运行命令：

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json --task-suite .\benchmarks\travel_task_group_a.json --max-completion-tokens 220 --max-retries 4 --retry-backoff-seconds 3 --max-tasks 2 --output-dir runs\v3-smoke-a1-a2-mimo25
```

结果：

| 指标 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| end_to_end_collaboration_tokens | 19341 | 10042 |
| llm_total_tokens | 14968 | 9370 |
| memory_hit_count | 0 | 2 |
| memory_write_count | 0 | 6 |
| claim_card_count | 0 | 6 |
| memory_view_count | 0 | 6 |
| promotion_view_count | 0 | 6 |
| unresolved_slot_count | 0 | 0 |
| alias_mapping_hit_count | 0 | 6 |
| retrieval_backend | 空 | `keyword_overlap_v3_lite` |

## Smoke：B1-B2

运行目录：`runs/v3-smoke-b1-b2-mimo25`

运行命令：

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json --task-suite .\benchmarks\security_task_group_b.json --max-completion-tokens 220 --max-retries 4 --retry-backoff-seconds 3 --max-tasks 2 --output-dir runs\v3-smoke-b1-b2-mimo25
```

结果：

| 指标 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| end_to_end_collaboration_tokens | 15542 | 8582 |
| llm_total_tokens | 14258 | 9520 |
| memory_hit_count | 0 | 2 |
| memory_write_count | 0 | 6 |
| claim_card_count | 0 | 6 |
| memory_view_count | 0 | 6 |
| promotion_view_count | 0 | 6 |
| unresolved_slot_count | 0 | 0 |
| alias_mapping_hit_count | 0 | 6 |
| retrieval_backend | 空 | `keyword_overlap_v3_lite` |

## 初步结论

v3-lite 已证明：

```text
1. 第一轮任务能产生 ClaimCard、MemoryView 和 PromotionView；
2. 后续相关任务能命中 MemoryView；
3. Agent 默认读取 Prompt View；
4. 最终任务已具备 Deliverable View 输入通道；
5. 静态 Alias Mapping 能避免 unresolved slot；
6. v3 指标已进入 metrics.csv / metrics.json / summary.json。
```

仍需完整验证：

```text
1. A1-A10 / B1-B10 完整重跑；
2. A10 / B10 质量裁判是否因 Deliverable View 改善；
3. Reviewer 发现最终交付缺字段后是否能触发修复；
4. Context-Pruned Retry-lite 是否能处理控制头格式错误；
5. 真实向量检索是否值得接入。
```
