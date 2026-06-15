# v0/v1 版本迭代实验结果记录

记录日期：2026-06-15  
实验代码版本：`d5a13eb`  
当前系统阶段：`v1 runtime lite`

本文记录从 v0 基线评测层到 v1 三线 Lite 闭环后的实验结果。当前实验使用确定性 Agent 任务流，目标是先验证指标口径、状态引用、共享记忆和端到端成本统计是否跑通；后续接入真实 LLM 和 AutoGen 等框架后，需要重新跑同一套 benchmark。

## 实验环境

| 项目 | 取值 |
| --- | --- |
| 操作系统 | Windows 本地开发环境 |
| Python | `F:\software\anaconda\envs\multi-agent-demo\python.exe` |
| Tokenizer | `tiktoken:cl100k_base` |
| tiktoken 版本 | `0.13.0` |
| Token 口径 | `token_count_method=compatible`，不使用字符估算 |
| 输出文件 | `trace.jsonl`、`metrics.csv`、`metrics.json`、`summary.json` |

端到端协作成本按以下公式记录：

```text
end_to_end_collaboration_tokens =
  direct_text_tokens
+ prompt_view_tokens
+ retrieved_memory_tokens
+ control_llm_tokens
+ retry_tokens
```

## 实验任务规模

| 项目 | 数值 |
| --- | ---: |
| 连续轮数 | 10 |
| 任务运行数 | 60 |
| Agent 调用数 | 240 |
| 每个任务 Agent 调用数 | 4 |
| 成功率 | 100% |

## v0 结果：基线与接口预埋

原始输出目录：`runs/v0-tokenizer-10round`

v0 的重点是建立可复现的纯文本基线、tokenizer-backed token 统计和指标导出。`runtime_stub` 只代表接口预埋，不代表真实低开销运行时收益。

| 模式 | task_runs | message_count | direct_text_tokens | prompt_tokens | avg_latency_ms | success_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_text` | 60 | 240 | 159140 | 131260 | 6.33 | 100% |
| `runtime_stub` | 60 | 240 | 159140 | 88990 | 6.03 | 100% |

v0 观察：

- `baseline_text` 给后续版本提供固定对照组。
- `runtime_stub` 的 prompt token 比 baseline 少 32.2%，但它尚未实现真实状态池、记忆复用或协议守卫，因此不能作为正式优化结论。
- v0 已确认中文任务不再使用 `chars / 4` 一类估算，避免中文 token 账本失真。

## v1 结果：SHP-lite + StatePool-lite + MemoryStore-lite

原始输出目录：`runs/v1-runtime-lite-gated-10round`

运行命令：

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v0_baseline.py --rounds 10 --mode both --output-dir runs\v1-runtime-lite-gated-10round
```

### 总体指标

| 模式 | task_runs | message_count | direct_text_tokens | prompt_view_tokens | retrieved_memory_tokens | end_to_end_collaboration_tokens | avg_latency_ms | success_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_text` | 60 | 240 | 159140 | 131260 | 0 | 290400 | 6.56 | 100% |
| `runtime_lite` | 60 | 240 | 52939 | 104773 | 15644 | 173356 | 12.69 | 100% |

相对 `baseline_text`：

| 指标 | 变化 |
| --- | ---: |
| 直接文本通信 token | 降低 66.7% |
| Prompt View token | 降低 20.2% |
| 端到端协作 token | 降低 40.3% |
| retrieved memory 占 runtime_lite 端到端成本 | 9.0% |
| 平均任务延迟 | 上升 93.3% |

v1 的核心结论是：即使把共享记忆读取 token 计入端到端成本，`runtime_lite` 仍然比纯文本 baseline 低 40.3%。这说明目前不是单纯把 Agent 间通信成本转移到 memory prompt 中，而是形成了净成本下降。

### 状态与记忆指标

| 指标 | 数值 |
| --- | ---: |
| state_refs_count | 240 |
| retrieval_state_count | 60 |
| artifact_state_count | 180 |
| embedding_state_count | 0 |
| memory_refs_count | 120 |
| memory_query_count | 59 |
| memory_query_hit_count | 59 |
| memory_hit_count | 118 |
| useful_memory_hit_count | 118 |
| wrong_memory_hit_count | 0 |
| memory_supported_output_count | 118 |
| memory_hit_rate | 100% |
| useful_memory_hit_rate | 100% |
| metrics 记录的 state_payload_bytes | 192646 |
| 本地 payload 文件数量 | 240 |
| 本地 payload 文件总大小 | 196366 bytes |

v1 已经落地了两类非文本/半结构化状态：

- `retrieval_state`：Retriever 产出的检索结构、证据列表、任务提示等。
- `artifact_state`：Planner、Writer、Reviewer 的中间产物，以 `state_ref` 传递，正文存入 StatePool。

`embedding_state` 仍为 0，这是 v2 必须补齐的点。比赛报告中不能只说“传 state_id”，需要展示 embedding、retrieval、artifact 三类状态的生成、传递、读取和后续使用方式。

## 当前判断

v1 已经证明三线 Lite 闭环具备初步效果：

- CMJCC 方向：通过 SHP-lite 控制 Agent 间直接文本传输，直接通信 token 降低 66.7%。
- SHP-State 方向：通过 file-backed StatePool 保存上游输出，并用 state refs 传递状态索引。
- TLC-Memory 方向：通过 MemoryStore-lite 在连续任务中复用 Writer/Reviewer 结论，并把 retrieved memory token 纳入总账。

同时，v1 也暴露出下一阶段必须处理的问题：

- `embedding_state_count=0`，非文本状态传递还不够硬，v2 要加入 embedding/chunk score/向量元数据。
- 当前 useful/wrong memory 指标仍是确定性任务中的乐观统计，v3 需要 Reviewer 或规则标注来区分真正有用记忆与错误命中。
- v1 的平均延迟从 6.56ms 上升到 12.69ms，说明 StatePool 文件写入和记忆检索确实引入运行时成本，v4 需要继续做生命周期治理、缓存和并发控制。
- 当前 Agent 是确定性模拟实现，v2/v3 接入真实 LLM 后，必须加入 Contract Guard、格式重试和 degraded fallback，防止模型输出格式破坏流水线。

## 下一个实验目标

v2 实验应重点验证：

- `embedding_state`、`retrieval_state`、`artifact_state` 三类状态全部出现。
- Contract Guard 能区分正常协议、可修复协议和 `degraded_fallback`，且 fallback 不会被误记为 `schema_valid=true`。
- 端到端成本继续包含 `retrieved_memory_tokens`、`control_llm_tokens`、`retry_tokens`，避免 cost shifting。
- 在 10 轮连续任务中，成功率保持 100%，同时协议错误不会导致下游 Agent 误用坏控制头。
