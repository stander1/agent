# v2 长上下文旅行任务实验结果记录

记录日期：2026-06-16  
实验代码阶段：`v2 long-context LLM evaluation`  
任务来源：`question/A.md` / `docs/problems/A.md`，任务组 A：长程个性化旅行规划与连续约束修订

本轮实验修正了旧 A1-A5 实验的问题：旧实验任务太短，且 baseline 没有持续累积跨任务完整历史，导致对低开销运行时的优势放大不足。新版 A1-A10 将旅行规划拆成 10 个连续修订任务，后半段不断加入晕车、出发时间、天气、预算下调、饮食限制和伴手礼预算等新约束。

关键修正：

```text
baseline_text:
  同一 round 内持续累积 A1-A10 所有 Agent 完整输出。
  后续任务 prompt 随历史线性增长。

runtime_lite:
  不携带完整历史正文。
  通过 StatePool、StateView、MemoryStore、MemoryView 复用前序结果。
```

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

实际 key 未写入仓库。

## 实验任务规模

原始输出目录：`runs/v2-longcontext-travel-a1-a10-1round-mimo25`

运行命令：

```powershell
$env:MIMO_API_KEY='本地环境变量中的 key'
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json --max-completion-tokens 300 --output-dir runs\v2-longcontext-travel-a1-a10-1round-mimo25
```

| 项目 | 数值 |
| --- | ---: |
| 任务 | A1-A10 |
| 轮数 | 1 |
| 模式 | `baseline_text` vs `runtime_lite` |
| Agent 数 | 5 |
| LLM 调用数 | 100 |
| 成功率 | 100% |

## 总体结果

| 模式 | task_runs | message_count | direct_text_tokens | prompt_view_tokens | retrieved_memory_tokens | llm_prompt_tokens | llm_completion_tokens | llm_total_tokens | end_to_end_collaboration_tokens | avg_latency_ms | success_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_text` | 10 | 50 | 22152 | 539370 | 0 | 368329 | 14669 | 382998 | 561522 | 28763.21 | 100% |
| `runtime_lite` | 10 | 50 | 13966 | 30687 | 3802 | 35458 | 13180 | 48638 | 48455 | 26743.19 | 100% |

相对 `baseline_text`：

| 指标 | 变化 |
| --- | ---: |
| 直接文本通信 token | 降低 37.0% |
| Prompt View token | 降低 94.3% |
| LLM prompt token | 降低 90.4% |
| LLM total token | 降低 87.3% |
| 端到端协作 token | 降低 91.4% |
| retrieved memory 占 runtime_lite 端到端成本 | 7.8% |
| 平均任务延迟 | 降低 7.0% |

## 随任务轮次增长的成本曲线

| task | baseline prompt_tokens | runtime_lite prompt_tokens | baseline llm_prompt_tokens | runtime_lite llm_prompt_tokens | baseline end_to_end | runtime_lite end_to_end |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A1 | 4329 | 2526 | 4262 | 3256 | 6163 | 3884 |
| A2 | 14214 | 3318 | 10524 | 3578 | 16567 | 5210 |
| A3 | 25839 | 3103 | 17977 | 3478 | 28156 | 4966 |
| A4 | 37095 | 3030 | 25514 | 3640 | 39276 | 4762 |
| A5 | 48270 | 3088 | 33069 | 3505 | 50537 | 4887 |
| A6 | 59502 | 3035 | 40525 | 3547 | 61656 | 4799 |
| A7 | 70786 | 3126 | 47840 | 3597 | 73126 | 4988 |
| A8 | 81851 | 3077 | 55353 | 3612 | 83979 | 4934 |
| A9 | 93067 | 3088 | 62897 | 3525 | 95353 | 4932 |
| A10 | 104417 | 3296 | 70368 | 3720 | 106709 | 5093 |

观察：

- baseline prompt 从 A1 的 4329 token 增长到 A10 的 104417 token。
- runtime_lite prompt 基本稳定在 2500-3300 token。
- A10 时，runtime_lite 的端到端协作 token 只有 baseline 的约 4.8%。

## 状态与记忆指标

| 指标 | runtime_lite 数值 |
| --- | ---: |
| state_refs_count | 50 |
| retrieval_state_count | 10 |
| artifact_state_count | 40 |
| embedding_state_count | 0 |
| memory_refs_count | 30 |
| memory_query_count | 9 |
| memory_query_hit_count | 9 |
| memory_hit_count | 18 |
| useful_memory_hit_count | 18 |
| wrong_memory_hit_count | 0 |
| memory_supported_output_count | 18 |
| state_payload_bytes | 44028 |
| 本地 payload 文件数量 | 50 |
| 本地 payload 文件总大小 | 44773 bytes |

## 结论

新版 A1-A10 更符合比赛第九题的连续协作压力场景。结果显示：

- 当任务需要连续复用历史时，纯文本协作的 prompt 成本会持续增长。
- runtime_lite 通过 StateRef/MemoryView 将上下文规模控制在稳定区间。
- 记忆读取没有造成明显 cost shifting，retrieved memory 只占 runtime_lite 端到端成本 7.8%。
- 与旧版 A1-A5 不同，本轮实验中 runtime_lite 的平均延迟也下降了 7.0%，说明当 baseline prompt 足够长时，减少 LLM 输入可以覆盖本地 State/Memory 开销。

当前仍需继续修正：

- `embedding_state_count=0`，还需要在 v3/v4 加入真实 embedding_state。
- SHP-lite 仍以 JSON 字符串传输，direct_text_tokens 只降低 37.0%，后续 compact SHP 还能继续压缩。
- useful/wrong memory 目前仍是规则统计，需要 Reviewer 标注或协议字段增强可信度。
- baseline 长历史修正后，旧 A1-A5 实验结果不再作为主结论，只作为早期短任务 smoke。

## 当前主结论口径

```text
在 MiMo v2.5 真实 LLM、A1-A10 长程旅行规划任务中，
runtime_lite 相比 baseline_text：

端到端协作 token 降低 91.4%，
LLM prompt token 降低 90.4%，
LLM total token 降低 87.3%，
平均任务延迟降低 7.0%，
成功率保持 100%。
```

## 重新实验补充

在修复 `artifact_state` 完整内容留档后，重新跑了一轮：

原始输出目录：`runs/v2-longcontext-travel-a1-a10-rerun2-quality-mimo25`

重新实验结果：

```text
direct_text_tokens 降低 38.9%
prompt_view_tokens 降低 94.6%
llm_prompt_tokens 降低 90.5%
llm_total_tokens 降低 87.4%
end_to_end_collaboration_tokens 降低 91.8%
avg_latency_ms 降低 7.3%
```

重新实验还加入了 MiMo 严格质量裁判。裁判结果显示 `runtime_lite` 在 A10 最终结果质量上优于 `baseline_text`，但两个版本都仍需补强预算表、决策日志和质量修复闭环。详见：[v2-longcontext-quality-judge.md](v2-longcontext-quality-judge.md)。
