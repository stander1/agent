# v3-lite 完整 A/B 连续任务实验结果

## 实验目的

本轮实验用于验证 v3-lite 是否解决 v2.3 暴露出的最终交付质量问题。

v2.3 已经证明 Hot/Warm/Cold State Pool 和 Cold audit payload 能显著降低通信成本，但 A10/B10 质量裁判多次指出 `runtime_lite` 的最终结果偏短，容易变成“摘要/复用要点”，不是完整交付物。

v3-lite 新增：

- PromotionView；
- ClaimCard；
- MemoryView；
- Alias Mapping；
- Deliverable View；
- 最终任务 LLM 指令约束。

本轮完整重跑：

```text
A1-A10: 长程个性化旅行规划
B1-B10: 合成安全审计链路追踪
```

## 运行环境

| 项 | A 组 | B 组 |
| --- | --- | --- |
| 输出目录 | `runs/v3-travel-a1-a10-mimo25` | `runs/v3-security-b1-b10-mimo25` |
| 任务集 | `benchmarks/travel_task_group_a.json` | `benchmarks/security_task_group_b.json` |
| 模型 | `mimo-v2.5` | `mimo-v2.5` |
| Base URL | `https://token-plan-cn.xiaomimimo.com/v1` | `https://token-plan-cn.xiaomimimo.com/v1` |
| Tokenizer | `tiktoken:cl100k_base 0.13.0` | `tiktoken:cl100k_base 0.13.0` |
| round | 1 | 1 |
| modes | `baseline_text`, `runtime_lite` | `baseline_text`, `runtime_lite` |

## A 组结果

### 成本指标

| 指标 | baseline_text | runtime_lite | runtime_lite 相对变化 |
| --- | ---: | ---: | ---: |
| direct_text_tokens | 21707 | 14812 | 降低 31.8% |
| prompt_view_tokens | 525433 | 57247 | 降低 89.1% |
| llm_prompt_tokens | 351283 | 54044 | 降低 84.6% |
| llm_total_tokens | 365591 | 67429 | 降低 81.6% |
| end_to_end_collaboration_tokens | 547140 | 85491 | 降低 84.4% |
| avg_latency_ms | 29364.4 | 34207.8 | 上升 16.5% |
| success_rate | 100% | 100% | 持平 |

### v3 记忆指标

| 指标 | runtime_lite |
| --- | ---: |
| memory_hit_count | 22 |
| memory_write_count | 30 |
| claim_card_count | 30 |
| memory_view_count | 30 |
| promotion_view_count | 30 |
| unresolved_slot_count | 0 |
| alias_mapping_hit_count | 30 |
| retrieved_memory_tokens | 13432 |
| retrieved_memory_share | 15.7% |
| retrieval_backend | `keyword_overlap_v3_lite` |

### A10 质量裁判

裁判文件：`runs/v3-travel-a1-a10-mimo25/quality_judge_mimo_a10_strict_1to5.json`

| 维度 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| requirement_coverage | 5 | 3 |
| constraint_consistency | 5 | 4 |
| actionability | 5 | 3 |
| budget_verifiability | 5 | 4 |
| decision_log_quality | 5 | 4 |
| deliverable_completeness | 4 | 3 |
| evidence_completeness | 3 | 3 |
| total | 32 | 24 |

结论：

```text
winner: baseline_text
baseline_total: 32
runtime_lite_total: 24
```

主要问题：

- runtime_lite 的 A10 输出仍偏抽象，缺少具体地点、活动时长、交通方式和可执行行程细节。
- Deliverable View 增加了最终任务上下文，但当前 MemoryView 内容仍偏“阶段摘要”，不足以恢复完整旅行手册。
- A 组需要领域化最终模板，例如预算表字段、每日行程字段、交通字段、餐饮字段、备选方案字段。

## B 组结果

### 成本指标

| 指标 | baseline_text | runtime_lite | runtime_lite 相对变化 |
| --- | ---: | ---: | ---: |
| direct_text_tokens | 18878 | 13130 | 降低 30.5% |
| prompt_view_tokens | 470355 | 41556 | 降低 91.2% |
| llm_prompt_tokens | 376789 | 47670 | 降低 87.4% |
| llm_total_tokens | 391616 | 61363 | 降低 84.3% |
| end_to_end_collaboration_tokens | 489233 | 63455 | 降低 87.0% |
| avg_latency_ms | 26759.5 | 25256.7 | 降低 5.6% |
| success_rate | 100% | 100% | 持平 |

### v3 记忆指标

| 指标 | runtime_lite |
| --- | ---: |
| memory_hit_count | 20 |
| memory_write_count | 30 |
| claim_card_count | 30 |
| memory_view_count | 30 |
| promotion_view_count | 30 |
| unresolved_slot_count | 0 |
| alias_mapping_hit_count | 30 |
| retrieved_memory_tokens | 8769 |
| retrieved_memory_share | 13.8% |
| retrieval_backend | `keyword_overlap_v3_lite` |

### B10 质量裁判

裁判文件：`runs/v3-security-b1-b10-mimo25/quality_judge_mimo_b10_strict_1to5.json`

| 维度 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| chain_coverage | 4 | 5 |
| evidence_traceability | 4 | 4 |
| deprecated_clue_handling | 5 | 4 |
| budget_constraint_handling | 4 | 5 |
| actionability | 4 | 5 |
| decision_log_quality | 4 | 4 |
| deliverable_completeness | 3 | 5 |
| total | 28 | 32 |

结论：

```text
winner: runtime_lite
baseline_total: 28
runtime_lite_total: 32
```

主要原因：

- runtime_lite 明确组织了最终审计手册、链路图谱、风险类型、证据表和决策日志。
- Deliverable View 对 B 组的证据链任务更有效，因为审计任务天然适合以 claim、evidence、decision log 组织。
- runtime_lite 在 B8 预算受限策略和系统可复用性上更强。

## 与 v2.3 对比

| 实验 | e2e 降低 | llm_total 降低 | 延迟变化 | runtime_e2e | retrieved_memory_share | claim_card_count | 质量裁判 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A v2.3 | 91.7% | 87.2% | 上升 9.8% | 48639 | 7.4% | 0 | baseline 胜 |
| A v3 | 84.4% | 81.6% | 上升 16.5% | 85491 | 15.7% | 30 | baseline 胜 |
| B v2.3 | 90.9% | 86.7% | 下降 0.7% | 42349 | 6.3% | 0 | baseline 胜 |
| B v3 | 87.0% | 84.3% | 下降 5.6% | 63455 | 13.8% | 30 | runtime_lite 胜 |

解释：

- v3 引入 Deliverable View 和更丰富 MemoryView 后，token 降幅低于 v2.3，但仍保持 80% 以上端到端下降。
- A 组质量没有改善，说明“通用 Deliverable View”不足以生成具体旅行手册。
- B 组质量反超，说明 v3 的 claim/evidence/memory-view 路线适合证据链型任务。

## 当前结论

v3-lite 证明：

```text
ClaimCard + MemoryView + Deliverable View
确实能改善部分连续任务的最终交付质量，
尤其是证据链、审计链、决策日志类任务。
```

但 v3-lite 也暴露：

```text
通用 Deliverable View 不能自动适配所有领域。
对 A 组旅行规划，仍需要领域化 Final Deliverable Schema。
```

## 下一步修正方向

建议进入 v3.1，而不是直接跳 v4。

v3.1 重点：

1. 为最终收束任务增加领域化 Deliverable Schema：
   - A10: itinerary_table, budget_table, food_constraints, fallback_plan, decision_log。
   - B10: chain_graph, evidence_table, deprecated_clue_policy, budget_policy, audit_decision_log。
2. Writer/MemoryManager 最终任务不能只压缩摘要，必须输出 schema-required fields。
3. Reviewer 如果发现最终交付缺字段，应触发一次修复。
4. 指标增加 `deliverable_schema_complete` 和 `final_quality_retry_count`。

这比继续扩大 MemoryView 更重要，因为 A 组失败不是记忆未命中，而是最终交付格式没有被强约束。
