# v2.3 任务组 B：合成安全审计链路压测结果

## 实验定位

任务组 B 用于验证 v2.3 在第二类连续任务上的表现。与任务组 A 的旅行规划不同，B 组强调：

- 多跳线索追踪；
- 合成审计证据链；
- deprecated clue 处理；
- Cold audit payload；
- 低控制预算下的证据裁剪；
- 最终审计手册与系统决策日志。

本实验仍使用 `baseline_text` 与 `runtime_lite` 双模式对比。

## 运行环境

| 项 | 值 |
| --- | --- |
| 运行目录 | `runs/v2.3-security-b1-b10-mimo25-rerun1` |
| 任务集 | `benchmarks/security_task_group_b.json` |
| 模型 | `mimo-v2.5` |
| Base URL | `https://token-plan-cn.xiaomimimo.com/v1` |
| Tokenizer | `tiktoken:cl100k_base 0.13.0` |
| 任务轮次 | B1-B10，1 round |
| 模式 | `baseline_text`, `runtime_lite` |

运行命令：

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json --task-suite .\benchmarks\security_task_group_b.json --max-completion-tokens 300 --max-retries 4 --retry-backoff-seconds 3 --output-dir runs\v2.3-security-b1-b10-mimo25-rerun1
```

## 总体结果

| 指标 | baseline_text | runtime_lite | runtime_lite 相对变化 |
| --- | ---: | ---: | ---: |
| direct_text_tokens | 18476 | 12344 | 降低 33.2% |
| prompt_view_tokens | 446268 | 27330 | 降低 93.9% |
| llm_prompt_tokens | 362465 | 36448 | 降低 89.9% |
| llm_total_tokens | 376947 | 50229 | 降低 86.7% |
| end_to_end_collaboration_tokens | 464744 | 42349 | 降低 90.9% |
| avg_latency_ms | 28393.2 | 28206.6 | 降低 0.7% |
| success_rate | 100% | 100% | 持平 |

观察：

- B 组也出现了明显的长上下文膨胀。baseline 的 prompt 从 B1 的 4126 token 增长到 B10 的 87400 token。
- runtime_lite 的 prompt 基本稳定在 2440 到 3192 token 之间。
- retrieved_memory_tokens 为 2675，占 runtime_lite 端到端成本约 6.3%，没有出现明显 cost shifting。
- 与 A 组 v2.3 不同，本轮 runtime_lite 平均延迟也略低于 baseline，但差距很小，应谨慎表述为“基本持平略低”。

## 分任务成本曲线

| 任务 | baseline prompt_view_tokens | runtime prompt_view_tokens | baseline e2e tokens | runtime e2e tokens |
| --- | ---: | ---: | ---: | ---: |
| B1 | 4126 | 2443 | 5765 | 3735 |
| B2 | 12254 | 2664 | 14019 | 4121 |
| B3 | 21139 | 2637 | 22933 | 4120 |
| B4 | 29890 | 2440 | 31555 | 3874 |
| B5 | 38542 | 2873 | 40384 | 4449 |
| B6 | 48430 | 2899 | 50557 | 4511 |
| B7 | 58645 | 2664 | 60618 | 4125 |
| B8 | 68310 | 2872 | 70151 | 4452 |
| B9 | 77532 | 2646 | 79447 | 4132 |
| B10 | 87400 | 3192 | 89315 | 4830 |

## 状态池分层验证

| 指标 | runtime_lite |
| --- | ---: |
| hot_state_count | 10 |
| warm_state_count | 0 |
| cold_state_count | 40 |
| hot_state_bytes | 10160 |
| cold_state_bytes | 83286 |
| cold metadata files | 40 |
| cold audit files | 40 |
| metadata content 字段命中 | 0 |
| audit content 字段命中 | 40 |

解释：

- 每个任务的检索/资料状态进入 Hot Tier。
- 每个非 retriever Agent 的完整产物进入 Cold audit payload。
- 普通 cold metadata 没有 `content` 字段，说明完整正文没有混入普通 Prompt View。

## B10 质量裁判

裁判文件：`runs/v2.3-security-b1-b10-mimo25-rerun1/quality_judge_mimo_b10_strict_1to5.json`

严格 1-5 分裁判结果：

| 维度 | baseline_text | runtime_lite |
| --- | ---: | ---: |
| chain_coverage | 5 | 3 |
| evidence_traceability | 5 | 3 |
| deprecated_clue_handling | 5 | 4 |
| budget_constraint_handling | 5 | 4 |
| actionability | 5 | 4 |
| decision_log_quality | 5 | 3 |
| deliverable_completeness | 5 | 3 |
| total | 35 | 28 |

裁判结论：

```text
winner: baseline_text
baseline_total: 35
runtime_lite_total: 28
```

主要原因：

- baseline_text 的 B10 输出更像完整审计手册，具体列出了链路、证据和系统决策日志。
- runtime_lite 的 B10 输出偏向“共享记忆摘要”和“下游复用要点”，对具体链路、证据表和最终交付展开不足。
- runtime_lite 对 B6 过期线索和 B8 低预算约束有提及，但不够实例化。

## 结论

v2.3 在 B 组上证明了：

```text
三层 State Pool + SHP state_ref + Prompt View 裁剪
可以在第二类连续任务中继续显著降低端到端协作 token，
并且没有把成本明显转移到 MemoryView 读取。
```

但 v2.3 还没有证明：

```text
低开销 runtime_lite 的最终交付质量稳定优于 baseline_text。
```

这与 A 组 v2.3 的结果一致。下一步 v3 的核心不应只是继续压 token，而应补齐：

- Promotion View；
- ClaimCard；
- MemoryView；
- 最终交付用的 Deliverable View；
- Reviewer 发现最终报告缺字段时触发修复；
- MemoryManager 不再替代最终交付，而是保存可复用摘要和证据索引。

## 对 v3 的直接启示

B 组暴露出的关键问题是：

```text
runtime_lite 成本低，但最终 Agent 看到的 Prompt View 过短时，
会倾向输出“方法论摘要”，而不是完整实例化交付物。
```

因此 v3 需要在低开销和质量之间增加一个中间层：

```text
State/Memory Prompt View:
  给普通中间 Agent，保持短。

Deliverable View:
  给最终 Writer/MemoryManager，用于 B10/A10 这类收束任务。
  它不展开全部 raw content，但必须包含最终交付所需的结构化证据表、修订日志和关键结论。
```
