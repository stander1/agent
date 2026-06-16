# 版本路线校准：创新方案 / 原规划 / 当前代码对照

## 1. 校准目的

本文用于防止后续开发出现：

```text
创新方案说一套；
版本规划写一套；
代码实现又临时走另一套。
```

从本文开始，后续新增能力必须先回答四个问题：

```text
1. 它属于三份最终创新方案中的哪一条线？
2. 它在原始版本路线中属于哪个版本？
3. 当前代码是否已经完整实现，还是只是 lite / guard / smoke？
4. 如果提前实现，是否只作为当前版本内部补强，而不是改变主版本路线？
```

三份最终方案对应三条能力线：

```text
CMJCC:
  低开销通信、Agent Output Contract、Contract Guard、能力路由、访问控制、控制预算。

SHP-State:
  Runtime State Pool、state_refs、State Access API、Prompt View、Promotion View、状态分层和 GC。

TLC-Memory:
  Memory Candidate、Claim Card、Memory Admission、MemoryView、生命周期和跨任务复用。
```

## 2. 原始主版本路线不变

主版本路线仍按 `docs/planning/README.md`：

| 主版本 | 原始定位 | 不能改变的边界 |
| --- | --- | --- |
| v0 | 基线与接口预埋 | 评测、tokenizer、state_ref / memory_ref 字段预埋 |
| v1 | 三线 Lite 最小完整闭环 | SHP-lite、StatePool-lite、MemoryStore-lite 必须同时存在 |
| v2 | 协议可靠化与真实状态引用 | Output Contract / Guard、真实 StateRef、MemoryRef 基础检索 |
| v3 | 连续任务复用 | Promotion View、ClaimCard、MemoryView、Alias Mapping、Context-Pruned Retry-lite |
| v4 | 联合成本治理与生命周期 | Retry Budget、Read Lease、GC、Lifecycle、Preflight Validation-lite |
| v5 | 跨框架适配与比赛验证 | AutoGen/LangGraph 等 Adapter、10 轮稳定压测、性能报告 |
| v6 | 研究增强 | 完整 CSCC、复杂 GC、eBPF/WASM/共享内存等增强 |

后续不再随意新增主线版本。`v3.1`、`v3.2` 这类编号只能解释为 `v3` 的内部补强快照。

## 3. 当前代码与主版本对照

| 当前快照 | Git 引用 | 归属主版本 | 实际实现 | 校准结论 |
| --- | --- | --- | --- | --- |
| v0 | `v0.0-baseline` | v0 | 纯文本 baseline、tokenizer、metrics、trace | 对齐 |
| v1 | `v1.0-runtime-lite` | v1 | SHP-lite、StatePool-lite、MemoryStore-lite | 对齐，但 SQLite 底座尚未完全替代 file/in-memory |
| v2 | `v2.0-llm-eval` | v2 | MiMo LLM eval、真实任务 A | 只实现 LLM eval，未完整实现 Output Contract Guard |
| v2.1 | `v2.1-longcontext-eval` | v2 | A1-A10 长上下文与基线历史 | 属于 v2 实验扩展，不是新机制主版本 |
| v2.2 | `v2.2-quality-rerun` | v2 | artifact 完整留档、网络重试、质量裁判 | 属于 v2 评测补强 |
| v2.3 | `v2.3-tiered-state` | v2 / v3 前置 | Hot/Warm/Cold State Pool、Cold audit payload | 状态分层前调合理，但应标为 v2 后期 / v3 前置 |
| v3.0 | `v3.0-memory-view-lite` | v3 | PromotionView、ClaimCard、MemoryView、Alias Mapping、Deliverable View | 对齐 v3 |
| v3.1 | `v3.1-final-schema` | v3 内部补强 | Final Deliverable Schema、Reviewer Guard、质量记录 | 属于 v3 最终交付质量补强，不改变主线 |
| v3.2 | `v3.2-reliability-guard` | v2 缺口补齐 + v3 稳定性守卫 | Provider Response Guard、Agent Output Contract Guard、degraded fallback | 这是对 v2 应有能力的补齐，同时服务 v3 连续任务稳定性 |

## 4. 已经发生的路线偏差

### 4.1 v2 的 Output Contract Guard 延后到 v3.2

原规划中，v2 应包含：

```text
Agent Output Contract
Control Header + Artifact Body
Output Contract Guard
规则修复 JSON
Schema 校验
默认值补全
fallback wrapper
schema_valid_rate / repair_success_count
```

实际情况：

```text
v2.0-v3.1 主要先做 LLM eval、状态池、记忆复用和最终 schema；
完整 Agent Output Contract Guard 到 v3.2 才落地。
```

校准解释：

```text
v3.2 不应被描述为新主线创新；
它是补齐 v2 协议可靠化缺口，并为 v3 连续任务稳定运行补守卫。
```

后续文档必须避免说：

```text
v3.2 新增了一套独立可靠性方案。
```

应说：

```text
v3.2 将原 v2/v3 规划中的 Output Contract Guard 和 Context-Pruned Retry-lite 工程化。
```

### 4.2 v3.1 Final Deliverable Schema 是 v3 内部质量补强

原 v3 目标是连续任务复用：

```text
Promotion View
ClaimCard
MemoryView
Alias Mapping
Context-Pruned Retry-lite
```

v3.1 做的：

```text
Final Deliverable Schema
Reviewer schema coverage
Final Schema Retry
```

校准解释：

```text
v3.1 是 v3 MemoryView / Deliverable View 的最终交付质量补强；
不是新主线，也不替代 Memory Admission 或 Lifecycle。
```

### 4.3 v3.3 不能叫“直接接 MemoryStore”

创新方案明确要求：

```text
memory_card / claim_cards 不是最终长期记忆；
Claim Card 是候选记忆单元；
Raw State 不能直接进入 Memory Pool；
高价值状态必须经过 Promotion View / Memory Candidate / Admission；
无法解析 slot_id 的 Claim 不能进入正式 MemoryView。
```

因此如果继续做 v3.3，名称和边界必须是：

```text
v3.3 Memory Candidate Admission Lite
```

而不是：

```text
v3.3 Contract output direct-to-MemoryStore
```

正确路径：

```text
Contract Guard control.memory_card / claim_cards
  ↓
Memory Capture Layer
  ↓
MemoryCandidate / ClaimCandidate
  ↓
Memory Admission Lite
  ↓
admitted candidates only
  ↓
MemoryStore / ClaimCard / MemoryView
```

## 5. 当前实现覆盖矩阵

| 能力 | 所属方案 | 原规划版本 | 当前状态 | 下一步要求 |
| --- | --- | --- | --- | --- |
| TokenCounter + 中英文 token 口径 | Eval / CMJCC | v0 | 已实现 | 继续保留 tokenizer meta |
| 纯文本 baseline | Eval | v0 | 已实现 | v5 继续作为对照组 |
| SHP-lite | CMJCC | v1 | 已实现 | 后续与 Contract Control Header 对齐 |
| StatePool-lite | SHP-State | v1 | 已实现 | SQLite/State Access API 仍需增强 |
| MemoryStore-lite | TLC-Memory | v1 | 已实现 | 需要 Candidate Admission 后再写入 |
| LLM eval harness | Eval | v2 | 已实现 | 保持 API key 仅环境变量 |
| Agent Output Contract | CMJCC | v2 | v3.2 已实现 lite | 后续让 memory_card/claim_cards 驱动候选池 |
| Output Contract Guard | CMJCC | v2 | v3.2 已实现 lite | 增加更多 schema 和 retry budget |
| Provider Response Guard | CMJCC / Runtime Reliability | v2 守卫补充 | v3.2 已实现 | 属于外层 envelope guard，不替代 Contract Guard |
| Hot/Warm/Cold State Pool | SHP-State | v2/v4 | v2.3 已实现 file-backed lite | 后续补 Read Lease / GC |
| Promotion View | SHP-State / TLC-Memory | v3 | 已实现 lite | 与 Memory Candidate 对接 |
| ClaimCard | TLC-Memory | v3 | 已实现 lite | 当前写入偏直接，需 Admission Gate |
| MemoryView | TLC-Memory | v3 | 已实现 lite | 需 lifecycle / conflict resolver |
| Alias Mapping | TLC-Memory | v3 | 已实现 lite | 继续避免粗粒度误合并 |
| Final Deliverable Schema | CMJCC / TLC-Memory view | v3 补强 | v3.1 已实现 | 不等同 Memory Admission |
| Context-Pruned Retry-lite | CMJCC | v3 | v3.2 部分实现 | 需接 Retry Budget |
| Memory Admission Gate | TLC-Memory | v3/v4 | 未完整实现 | 下一步若继续 v3，必须优先做 |
| Memory Candidate Pool | TLC-Memory / SHP-State Bridge | v3 | 未完整实现 | 下一步 v3.3 的正确目标 |
| Lifecycle Status | TLC-Memory | v4 | 未实现 | v4 做 |
| Preflight Validation-lite | TLC-Memory / CMJCC | v4 | 未实现 | v4 做，防高成本重写 |
| Read Lease-lite | SHP-State | v4 | 未实现 | v4 与 GC 同步做 |
| State GC / Tombstone | SHP-State | v4 | 未实现 | v4 做 |
| Retry Budget | CMJCC | v4 | 未实现 | v4 做 |
| AutoGen / LangGraph Adapter | Cross-framework | v5 | 未实现 | v5 做 |
| openEuler 复现脚本 | Competition | v5 | 未实现 | v5 做 |

## 6. 后续版本命名规则

为避免再出现临场拆小版本，后续版本命名遵循：

```text
主版本号只按原规划推进：v0-v6。
小版本号只能表示同一主版本内的工程补强。
每个小版本必须在本文中登记：
  - 归属主版本
  - 对应创新方案章节
  - 为什么需要现在做
  - 是否改变后续主版本边界
```

已登记的小版本解释：

| 小版本 | 归属主版本 | 合法解释 |
| --- | --- | --- |
| v2.1 | v2 | 长上下文连续任务实验扩展 |
| v2.2 | v2 | 质量裁判和 artifact 留档补强 |
| v2.3 | v2 / v3 前置 | StatePool 分层前调 |
| v3.1 | v3 | Final Deliverable Schema 质量补强 |
| v3.2 | v2 缺口补齐 / v3 稳定性守卫 | Output Reliability Guard 工程化 |

未来若出现：

```text
v3.3 Memory Candidate Admission Lite
```

必须满足：

```text
1. 不直接把 memory_card 写入长期 MemoryStore；
2. 必须有 MemoryCandidate / ClaimCandidate；
3. 必须有 Admission 状态；
4. 必须有 rejected / pending / unresolved_slot / admitted 指标；
5. 通过准入后才更新 MemoryView。
```

## 7. 下一步推荐路线

当前主干已经到：

```text
v3.2 = v3 主线 + v2 可靠性缺口补齐
```

下一步有两个选择。

### 选择 A：继续补齐 v3 主线

名称：

```text
v3.3 Memory Candidate Admission Lite
```

目标：

```text
把 Contract Guard 产出的 memory_card / claim_cards 变成候选；
通过规则优先的 Admission Gate 判断；
只让 admitted candidate 进入 MemoryStore / MemoryView。
```

这是最符合当前状态的下一步，因为 v3 的 TLC-Memory 仍缺候选准入层。

### 选择 B：冻结 v3，进入 v4

前提：

```text
明确 v3 当前是 lite 版；
承认 Memory Admission 仍是 v4 前置或 v3 未完成项；
进入 Retry Budget / Read Lease / GC / Preflight Validation。
```

风险：

```text
如果不补 Candidate Admission，TLC-Memory 容易被质疑“Claim 直接入池，缺少生命周期准入”。
```

因此推荐：

```text
先做选择 A，再进 v4。
```

## 8. 答辩口径校准

当前版本叙事应改为：

```text
我们采用三线并行加深路线。
v0-v1 建立完整闭环；
v2 进入真实 LLM 和协议可靠化，但 Output Guard 在 v3.2 才补齐工程实现；
v3 聚焦连续任务复用，已实现 ClaimCard / MemoryView / Alias Mapping / Deliverable View；
v3.1 和 v3.2 是 v3 的质量与稳定性补强；
下一步补 Memory Candidate Admission，随后进入 v4 的成本治理和生命周期。
```

不要说：

```text
每个小版本都代表一个新创新点。
```

应该说：

```text
小版本是为了把原创新方案中已规划的 guard / admission / schema 能力补到可运行原型中。
```

## 9. 后续开发检查清单

每次新开发前必须检查：

```text
1. 是否能在三份最终方案中找到对应模块？
2. 是否能在主版本规划表中找到对应版本？
3. 是否会绕过候选池、准入门控、状态晋升等关键中间层？
4. 是否会把实验补丁伪装成主线机制？
5. 是否新增了指标证明收益或风险？
6. 是否需要更新本文的覆盖矩阵？
```

如果第 1 或第 2 条无法回答，就先暂停，不写代码。
