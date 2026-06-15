# v2 LLM 旅行任务实验结果记录

记录日期：2026-06-16  
实验代码阶段：`v2 llm evaluation harness`  
任务来源：`question/A.md`，任务组 A：个性化旅行规划与预算优化

v2 的目标是把真实 LLM 接入实验层，而不是接入核心 runtime。核心 runtime 仍然不持有 API key；API key 只通过本地环境变量传入 `examples/run_v2_llm_eval.py`。

## LLM 接入方式

| 项目 | 取值 |
| --- | --- |
| Provider | MiMo Token Plan |
| Base URL | `https://token-plan-cn.xiaomimimo.com/v1` |
| API 形态 | OpenAI-compatible `chat/completions` |
| Model | `mimo-v2.5` |
| API key | `MIMO_API_KEY` 环境变量 |
| max_completion_tokens | 300 |
| thinking | disabled |
| Tokenizer 记账 | `tiktoken:cl100k_base` |

说明：

- `tp-` 开头的开发者计划 key 走 Token Plan URL。
- 当前 v2 runner 使用 `/v1` OpenAI-compatible 接口。
- `https://token-plan-cn.xiaomimimo.com/anthropic` 是 Anthropic-compatible 接口，后续可扩展为另一个 provider adapter。
- 实际 key 不写入仓库，不写入示例配置，不进入提交历史。

参考文档：

- MiMo Token Plan Subscription Instructions: <https://mimo.mi.com/docs/en-US/tokenplan/Token%20Plan/subscription>
- MiMo OpenAI API Compatibility: <https://platform.xiaomimimo.com/docs/en-US/api/chat/openai-api>
- MiMo Deep Thinking 参数说明: <https://mimo.mi.com/docs/usage-guide/passing-back-reasoning_content>

## 实验任务规模

原始输出目录：`runs/v2-llm-travel-a1-a5-1round-mimo25`

运行命令：

```powershell
$env:MIMO_API_KEY='本地环境变量中的 key'
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json --max-completion-tokens 300 --output-dir runs\v2-llm-travel-a1-a5-1round-mimo25
```

| 项目 | 数值 |
| --- | ---: |
| 任务 | A1-A5 |
| 轮数 | 1 |
| 模式 | `baseline_text` vs `runtime_lite` |
| Agent 数 | 5 |
| LLM 调用数 | 50 |
| 成功率 | 100% |

## 总体结果

| 模式 | task_runs | message_count | direct_text_tokens | prompt_view_tokens | retrieved_memory_tokens | llm_prompt_tokens | llm_completion_tokens | llm_total_tokens | end_to_end_collaboration_tokens | avg_latency_ms | success_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_text` | 5 | 25 | 10089 | 22131 | 0 | 20348 | 6624 | 26972 | 32220 | 33915.75 | 100% |
| `runtime_lite` | 5 | 25 | 6928 | 14843 | 1630 | 17300 | 6331 | 23631 | 23401 | 36454.87 | 100% |

相对 `baseline_text`：

| 指标 | 变化 |
| --- | ---: |
| 直接文本通信 token | 降低 31.3% |
| Prompt View token | 降低 32.9% |
| LLM prompt token | 降低 15.0% |
| LLM total token | 降低 12.4% |
| 端到端协作 token | 降低 27.4% |
| retrieved memory 占 runtime_lite 端到端成本 | 7.0% |
| 平均任务延迟 | 上升 7.5% |

## 状态与记忆指标

| 指标 | runtime_lite 数值 |
| --- | ---: |
| state_refs_count | 25 |
| retrieval_state_count | 5 |
| artifact_state_count | 20 |
| embedding_state_count | 0 |
| memory_refs_count | 15 |
| memory_query_count | 4 |
| memory_query_hit_count | 4 |
| memory_hit_count | 8 |
| useful_memory_hit_count | 8 |
| wrong_memory_hit_count | 0 |
| memory_supported_output_count | 8 |
| state_payload_bytes | 21973 |
| 本地 payload 文件数量 | 25 |
| 本地 payload 文件总大小 | 22332 bytes |

## 观察结论

v2 已经比 v1 更接近真实比赛评测：

- 真实 LLM 参与后，`runtime_lite` 仍然把端到端协作 token 降低了 27.4%。
- LLM prompt tokens 下降 15.0%，说明 StateRef/MemoryView 对真实模型输入也有效。
- retrieved memory tokens 占 runtime_lite 总成本 7.0%，没有出现明显 cost shifting。
- A2-A5 的 runtime_lite 能读取 A1-A4 写入的记忆和状态，任务连续性明显好于 baseline。

也暴露出几个必须继续优化的问题：

- 平均任务延迟上升 7.5%，说明真实 LLM 响应波动、JSON 状态写入和 Prompt View 构造仍会带来时间成本。
- `embedding_state_count=0`，v2 当前仍未补齐真实 embedding_state，后续必须加入向量元数据和 chunk embedding refs。
- `runtime_lite` 的 SHP JSON 仍偏长，直接消息 token 虽低于 baseline，但还可通过压缩字段名、短摘要和二进制/表格化控制头继续下降。
- 当前 memory useful/wrong 指标仍以规则统计为主，需要 Reviewer 标注或协议字段来提升可信度。

## 与 v1 的关系

v1 用确定性 Agent 验证运行时闭环和指标口径；v2 使用真实 LLM 验证同一机制在模型调用下是否仍然有效。v2 的 API key 和模型配置属于实验 runner，不属于核心 runtime。

当前结论：

```text
核心 runtime 不绑定 LLM；
v2 LLM harness 只用于真实评测；
后续 AutoGen adapter 仍应使用 AutoGen 自己的 LLM 配置。
```
