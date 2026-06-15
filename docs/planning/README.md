# 跨框架多 Agent 低开销运行时：三线并行版本迭代总览

## 1. 项目基调

本项目最终交付物不是一个新的通用 Agent 框架，也不是单纯的 Prompt 压缩器，而是一套可以被 AutoGen、LangGraph、自研 Agent 等多种 Agent 架构接入的运行时工具层。

项目目标是：

```text
在多 Agent 协作过程中，通过结构化通信、非文本状态传递和共享记忆复用，显著降低 Agent 间通信 token、重复文本解析、重复检索、重复总结和跨任务冷启动成本。
```

比赛版需要满足第九题要求：

```text
1. 不少于 3 个 Agent 协同运行；
2. 同时支持纯文本协作模式和结构化协议协作模式；
3. 实现结构化通信协议；
4. 实现非文本中间状态传递机制；
5. 实现共享记忆存储、检索和复用；
6. 至少 2 组关联连续任务；
7. 稳定执行不少于 10 轮连续任务；
8. 展示通信开销、任务时延、记忆复用等性能对比数据；
9. 最终能在 openEuler 24.03-LTS-SP3 上运行、测试、复现。
```

## 2. 三份最终方案的工程拆解

三份最终方案不应作为三个彼此孤立的大系统分别实现，而应合并为一个运行时内核中的三个能力面：

```text
CMJCC:
  负责低开销通信、输出契约、能力路由、访问控制、冷数据治理和控制预算。

SHP-State:
  负责 Runtime State Pool、state_refs、State Access API、Prompt View Renderer 和状态回收。

TLC-Memory:
  负责 Claim Card、MemoryView、生命周期状态、Prompt/Audit/Storage View 和跨任务记忆复用。
```

三者关系：

```text
CMJCC 让 Agent 少说、少读、少重复控制；
SHP-State 让当前任务的中间结果不要反复文本化；
TLC-Memory 让未来任务不用从头做，且避免记忆污染。
```

系统技术与赛题鼓励项的具体映射见：

[系统技术选型与赛题映射.md](</C:/Users/千梦/Desktop/ClaudeCode/操作系统开源创新大赛/跨框架运行时系统版本规划/系统技术选型与赛题映射.md>)

从 0 到 1 的具体开发计划见：

[从0到1开发计划说明书.md](</C:/Users/千梦/Desktop/ClaudeCode/操作系统开源创新大赛/跨框架运行时系统版本规划/从0到1开发计划说明书.md>)

## 3. 迭代原则：三线并行，而不是单线追加

为了保持整套系统的完整性，版本迭代不采用“先做通信，再做状态，再做记忆”的串行方式，而采用“三线并行加深”的方式。

也就是说，从最早的可运行原型开始，系统里就同时存在：

```text
CMJCC-lite:
  最小结构化通信和成本指标。

SHP-State-lite:
  最小 Runtime State Pool、state_refs 和 Prompt View。

TLC-Memory-lite:
  最小 Claim / MemoryRef / MemoryView / 复用接口。
```

后续版本不是“新加一个方案”，而是让三个方案同时从 lite 版本演进到比赛可交付版本。

这样更适合答辩叙事：

```text
我们从 v1 起就是一套完整的低开销多 Agent 运行时；
v2-v5 只是逐步增强通信可靠性、状态复用深度、记忆治理能力和跨框架接入能力。
```

## 4. 推荐版本路线

| 版本 | 目标 | CMJCC | SHP-State | TLC-Memory | 是否可演示 |
| --- | --- | --- | --- | --- | --- |
| v0 | 基线与接口预埋 | 成本指标 | state_ref 字段预埋 | memory_ref 字段预埋 | 是 |
| v1 | 最小完整闭环 | SHP-lite / Router-lite / Transport 抽象 | StatePool-lite / PromptView-lite / file-ref | MemoryStore-lite / Claim-lite | 是 |
| v2 | 协议和状态真实化 | Output Contract / Guard | 真实 state_refs / State Access API / payload backend 预留 | MemoryRef / 基础检索 | 是 |
| v3 | 连续任务复用 | 通信门控 / 访问策略 / Context-Pruned Retry-lite | Promotion View | ClaimCard / MemoryView / Alias Mapping / 向量检索 | 是 |
| v4 | 联合成本治理 | Admission / Budget / Digest / Retry Budget | Read Lease-lite / GC / mmap 或 shared-memory backend | Lifecycle / Preflight Validation-lite / Prompt-Audit View | 是 |
| v5 | 比赛交付版 | 双模式对比 / 报告 / IPC-Socket 接入边界 | 10 轮稳定状态复用 / file-ref 或 mmap 演示 | 2 组连续任务记忆命中 / 向量检索 | 是 |
| v6 | 研究增强版 | Cold Access / Escalation 完整版 / eBPF 观测 | Fencing / CF-aware GC / shared memory 完整版 | Soft Deprecation / CSCC 完整版 / 沙箱增强 | 可选 |

推荐比赛主线到 v5 为止。v6 作为答辩和后续开源扩展，不应拖累比赛最小可交付。

## 5. 版本依赖关系

```text
v0 基线和三线接口预埋
  ↓
v1 三线 lite 最小完整闭环
  ↓
v2 协议可靠化 + 状态真实引用
  ↓
v3 记忆复用和连续任务收益
  ↓
v4 联合成本治理和生命周期增强
  ↓
v5 跨框架适配 + 比赛验证
  ↓
v6 高级治理和系统技术增强
```

不能跳过 v0。因为本赛题评分高度依赖“相比纯文本协作的 token 节省效果”和“实验验证说服力”，所以评测框架必须先于复杂模块搭建。

## 6. 实现原则

```text
1. 先做可运行闭环，再加复杂治理。
2. 每个版本都保留 plain_text_mode 与 optimized_mode 对比。
3. 所有优化都必须进入指标系统，不做无法证明收益的模块。
4. 运行时核心不绑定某个 Agent 框架，框架只通过 Adapter 接入。
5. 比赛版优先用 SQLite、JSONL、本地文件和轻量向量检索，避免过早引入重型基础设施。
6. hidden state / KV cache 只作为论文讨论，不作为 API-only 原型主路线。
7. eBPF、WASM、容器沙箱、冷 I/O 调度等只做可选增强，不放进主路径。
8. v0 就必须接入 TokenCounter；真实 tokenizer 优先，兼容 tokenizer 次之，中英混合估算只作兜底，并记录 token_count_method、tokenizer_name、tokenizer_version。
9. SQLite 从 v1 起启用 WAL、busy_timeout 和短事务；高频运行时计数优先放内存 RuntimeRegistry，避免读租约等热路径造成 database is locked。
```

## 7. 关键守卫前调原则

有些机制看起来像研究增强，但对 v5 的 10 轮稳定运行是必要守卫，因此必须前调 lite 版：

```text
Context-Pruned Retry-lite:
  v3 引入。控制头规则修复失败时，不继承完整 Artifact，只用短上下文修复控制头，避免开源模型格式错误打断流水线。

Read Lease-lite:
  v4 与 State GC 同步引入。只要状态可能被 GC 或 tombstone，就必须有 active_readers 计数，避免读取中被删除。

Preflight Validation-lite:
  v4 引入。WriterAgent 启动高成本长生成前，先检查 memory_ref / read_set 是否 active 且非 pending transition，避免生成完成后整段回滚。

Alias Mapping Dict:
  v3 与 Schema Registry 简化版同步引入。优先用静态别名、字符串归一化和编辑距离做 slot 对齐，避免为了本体对齐频繁调用 LLM。
```

这些 lite 机制只实现比赛所需的最小闭环；v6 再实现完整的 fencing token、复杂 CSCC、冷访问调度和控制流感知 GC。

## 8. 建议仓库结构

```text
agent_runtime/
  core/
    runtime.py
    adapter.py
    scheduler.py
    events.py
  protocol/
    shp.py
    output_contract.py
    contract_guard.py
    capability.py
  state/
    state_pool.py
    state_access.py
    prompt_view.py
    state_gc.py
  memory/
    claim_card.py
    schema_registry.py
    memory_view.py
    lifecycle.py
    retrieval.py
  adapters/
    autogen_adapter.py
    custom_adapter.py
  eval/
    metrics.py
    benchmark_runner.py
    report.py
  examples/
    tasks_research_pipeline.py
    tasks_code_report.py
```

## 9. 最终比赛演示建议

演示不要只展示一个漂亮流程，而要展示对比：

```text
同一组连续任务：
  A. 纯文本协作模式；
  B. 结构化协议 + 状态引用 + 共享记忆模式。

展示指标：
  message_count
  direct_text_chars / estimated_tokens
  prompt_tokens_rendered
  state_refs_count
  state_reuse_count
  memory_hit_rate
  raw_access_count
task_latency
successful_round_count
```

其中成本图建议使用堆叠柱状图，而不是只给总 token：

```text
Baseline:
  direct_text_tokens

Optimized:
  handoff_packet_tokens
  prompt_view_tokens
  retrieved_memory_tokens
  control_llm_tokens
```

这样可以证明系统不是把直接通信成本转移到记忆读取成本，而是真的降低端到端协作成本。

演示叙事：

```text
第一轮任务中，系统产生状态和记忆；
第二轮相关任务中，Agent 不再重复检索/重复总结，而是通过 state_refs 和 memory_refs 复用；
多轮运行后，系统展示 token 节省、状态复用、记忆命中和耗时变化。
```

## 10. 为什么不用串行追加方案

串行追加方案的风险是：

```text
v2 看起来只是通信协议；
v3 看起来才出现状态；
v4 看起来才出现记忆；
早期版本不像完整系统；
答辩时容易被质疑三个创新点是后期拼装。
```

三线并行方案的优势是：

```text
从 v1 起就有完整系统闭环；
每个版本都能跑三类机制；
实验指标可以持续累积；
系统架构边界更稳定；
后续增强不会推翻前面版本。
```
