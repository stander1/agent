# v2.1 长上下文实验质量裁判记录

记录日期：2026-06-16  
关联实验：`runs/v2-longcontext-travel-a1-a10-1round-mimo25`  
裁判模型：`mimo-v2.5`

本记录补充说明：长上下文实验已经证明 `runtime_lite` 在 token 和延迟上显著优于 `baseline_text`，但“成功率 100%”不等于“最终结果质量一定更好”。因此在实验后使用 MiMo 对 A10 最终任务结果进行了质量裁判。

## 当前实验逻辑

### baseline_text

当前修正后的 baseline 逻辑是：

- 同一 round 内持续累积 A1-A10 的所有 Agent 完整输出。
- 每个后续 task 的每个 Agent prompt 都包含当前任务请求和历史完整自然语言上下文。
- 不使用 `state_ref`、`memory_ref`、StateView 或 MemoryView。
- 因此 prompt token 随任务轮次持续增长。

### runtime_lite

当前 runtime_lite 逻辑是：

- Agent 间消息只传 SHP-lite JSON、短摘要、`state_refs` 和 `memory_refs`。
- 完整中间产物写入 StatePool。
- 下游 Agent 通过 StateView/MemoryView 读取裁剪后的上下文。
- MemoryStore 在 A2-A10 中进行检索复用。
- 当前版本的 StatePool 在本次实验时只保存 artifact 摘要，未保存完整 Agent 输出正文；这是质量审计缺陷。

## 成本结果回顾

| 指标 | baseline_text | runtime_lite | runtime_lite 相对变化 |
| --- | ---: | ---: | ---: |
| prompt_view_tokens | 539370 | 30687 | 降低 94.3% |
| llm_prompt_tokens | 368329 | 35458 | 降低 90.4% |
| llm_total_tokens | 382998 | 48638 | 降低 87.3% |
| end_to_end_collaboration_tokens | 561522 | 48455 | 降低 91.4% |
| avg_latency_ms | 28763.21 | 26743.19 | 降低 7.0% |

成本结论成立：`runtime_lite` 在长上下文场景中显著降低协作成本。

## 质量裁判设置

裁判只评价 A10 最终任务质量，不因为 token 低、协议结构化或运行效率而加分。

评分维度，每项 0-5 分：

- requirement_coverage
- constraint_consistency
- actionability
- budget_verifiability
- decision_log_quality
- deliverable_completeness
- evidence_completeness

A10 必须覆盖：

```text
最终旅行手册；
每日安排；
预算表；
注意事项；
备选方案；
决策日志；
3天2晚；
预算2600以内；
自然风景、轻徒步、当地美食；
规避人多商业景点；
低强度；
晕车；
第一天10点后出发；
第二天下雨备选；
不吃辣；
每天至少一顿当地特色餐；
预留200元伴手礼。
```

## 严格裁判结果

MiMo 严格质量裁判输出：

```json
{
  "winner": "baseline_text",
  "baseline_total": 24,
  "runtime_lite_total": 11,
  "verdict": "baseline_text 显著优于 runtime_lite"
}
```

分项结果：

| 维度 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| requirement_coverage | 4 | 2 |
| constraint_consistency | 4 | 2 |
| actionability | 4 | 2 |
| budget_verifiability | 3 | 1 |
| decision_log_quality | 2 | 1 |
| deliverable_completeness | 3 | 1 |
| evidence_completeness | 4 | 2 |
| total | 24 | 11 |

裁判指出：

- baseline_text 提供了更具体的每日行程安排，包括时间、地点和活动。
- baseline_text 至少呈现了预算表结构，虽然存在截断和缺失。
- runtime_lite 可见证据主要是 SHP 短包和 StatePool 摘要，缺少完整最终手册正文。
- runtime_lite 的可见 Writer 摘要存在占位符，例如 `XX公园`，不适合直接作为最终旅行手册。
- 两种模式都存在决策日志不完整问题。

## 结论修正

当前不能只说：

```text
runtime_lite 成功率 100%，所以质量也没问题。
```

更准确的结论是：

```text
runtime_lite 在长上下文实验中显著降低 token 和延迟成本；
但当前实验留档不足，无法证明最终交付质量优于 baseline；
在严格可见证据裁判下，baseline_text 的最终可见答案质量更高。
```

因此，当前实验的主张应分开：

- 成本主张：成立。
- 稳定运行主张：成立。
- 最终质量主张：尚未成立。

## 必须修复的问题

1. StatePool 必须保存完整 artifact 内容，而不是只保存摘要。
2. trace 或独立 artifact report 必须能导出每个模式的最终 Writer 输出和 Reviewer 评价。
3. A10 质量评测必须作为固定评测步骤，不能只看 success_rate。
4. runtime_lite 的 Writer 不能输出占位符，最终手册必须包含具体预算表和决策日志。
5. Reviewer 如果发现“决策日志缺失”，系统应触发修复或重试，而不是仍然标记最终任务质量通过。

## 已做的代码修正

本记录之后，代码已修改：后续 `artifact_state` 会保存完整 `content` 字段，便于重新实验后进行同口径质量裁判。

注意：本次质量裁判基于旧实验输出，因此 runtime_lite 证据不完整。需要重新跑一轮 A1-A10 后，才能获得更公平的质量裁判。

## 重新实验后的质量裁判

重新实验目录：`runs/v2-longcontext-travel-a1-a10-rerun2-quality-mimo25`

本轮在 `artifact_state` 中保存了完整 `content` 字段，因此 `runtime_lite` 的 A10 Writer/Reviewer/MemoryManager 输出可以从 StatePool 恢复，质量裁判证据更完整。

### 成本结果

| 指标 | baseline_text | runtime_lite | runtime_lite 相对变化 |
| --- | ---: | ---: | ---: |
| direct_text_tokens | 22564 | 13783 | 降低 38.9% |
| prompt_view_tokens | 551828 | 30012 | 降低 94.6% |
| llm_prompt_tokens | 368543 | 35136 | 降低 90.5% |
| llm_total_tokens | 383227 | 48463 | 降低 87.4% |
| end_to_end_collaboration_tokens | 574392 | 47374 | 降低 91.8% |
| avg_latency_ms | 30510.40 | 28295.78 | 降低 7.3% |

### MiMo 严格质量裁判

裁判模型：`mimo-v2.5`

裁判要求：只评价 A10 最终结果质量，不评价 token 成本，不因结构化协议加分。

裁判结果：

```json
{
  "winner": "runtime_lite",
  "baseline_total": 15,
  "runtime_lite_total": 23,
  "verdict": "runtime_lite 显著优于 baseline"
}
```

分项结果：

| 维度 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| requirement_coverage | 3 | 4 |
| constraint_consistency | 3 | 4 |
| actionability | 2 | 3 |
| budget_verifiability | 1 | 3 |
| decision_log_quality | 2 | 3 |
| deliverable_completeness | 2 | 3 |
| evidence_completeness | 2 | 3 |
| total | 15 | 23 |

裁判给出的主要理由：

- `runtime_lite` 的 Planner 任务拆解更清晰，明确要求整合 A1-A9、每日安排、预算表和决策日志。
- `runtime_lite` 的 Writer 产物包含预算表和决策日志框架，比 baseline 更接近 A10 要求。
- `runtime_lite` 的 Reviewer 给出更具体的修正路径，而 baseline 的最终产物更缺少预算表与决策日志。

裁判同时指出质量风险：

- 两个版本都没有达到完美最终交付，预算明细和决策日志仍需更完整。
- `runtime_lite` 的预算解释仍需要校验，尤其是伴手礼预算和总预算口径。
- MemoryManager 输出仍偏概括，没有充分保存 A1-A9 每次修改的具体理由。

## 当前质量结论

重新实验后，结论可以更新为：

```text
runtime_lite 不仅显著降低长上下文协作成本，
在可见 A10 最终输出质量上也优于 baseline_text；
但最终交付质量仍未达到可直接比赛展示的理想水平。
```

因此后续版本需要加入质量闭环：

- Reviewer 发现预算表或决策日志缺失时，应触发 Writer 修复或格式重试。
- A10 必须增加质量评分字段，不能只记录 success_rate。
- MemoryManager 应保存更细的 revision log，而不是只写概括摘要。
