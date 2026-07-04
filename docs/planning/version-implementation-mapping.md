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
| v3.3 | `v3.3-memory-admission` | v3 内部补齐 | MemoryCandidate、ClaimCandidate、Admission Lite、admitted-only MemoryView 更新、准入状态指标 | 对齐 TLC-Memory 的候选池与准入门控，不改变主线 |
| v4.0 | `v4.0-cost-lifecycle` | v4 | Retry Budget、Read Lease-lite、State GC-lite、tombstone、Memory lifecycle、Preflight Validation-lite | 对齐 v4 联合成本治理与生命周期 lite |

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

v3.3 已按以下名称和边界落地：

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
| MemoryStore-lite | TLC-Memory | v1 | 已实现 | v3.3 已接 Candidate Admission，后续补生命周期 |
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
| Context-Pruned Retry-lite | CMJCC | v3 | v4.0 已接 Retry Budget-lite | v6 再扩完整 retry budget policy |
| Memory Admission Gate | TLC-Memory | v3/v4 | v3.3 已实现 lite | v4.0 已接 lifecycle / preflight-lite |
| Memory Candidate Pool | TLC-Memory / SHP-State Bridge | v3 | v3.3 已实现 lite | 已接 Contract Guard memory_card / claim_cards |
| Lifecycle Status | TLC-Memory | v4 | v4.0 已实现 lite | 后续补 conflict resolver / soft deprecation |
| Preflight Validation-lite | TLC-Memory / CMJCC | v4 | v4.0 已实现 lite | 后续扩大到更多高成本 Agent |
| Read Lease-lite | SHP-State | v4 | v4.0 已实现 lite | v6 再做 fencing token |
| State GC / Tombstone | SHP-State | v4 | v4.0 已实现 lite | 后续补控制流感知 GC |
| Retry Budget | CMJCC | v4 | v4.0 已实现 lite | 后续补 per-agent budget policy |
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
| v3.3 | v3 | Memory Candidate Admission Lite，补齐 TLC-Memory 候选池与准入门控 |
| v4.0 | v4 | Cost Governance and Lifecycle Lite，补齐 Retry Budget / Read Lease / GC / Preflight |

已登记并实现：

```text
v3.3 Memory Candidate Admission Lite
```

v3.3 已满足：

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
v4.0 = v3 主线 + v2 可靠性缺口补齐 + TLC-Memory 候选准入层 + v4 成本/生命周期守卫
```

选择 A 和 v4.0 lite 均已完成，下一步不再继续扩展 v3/v4 小版本，而是进入 v5。

### 已完成：选择 A 补齐 v3 主线

名称：

```text
v3.3 Memory Candidate Admission Lite
```

已实现目标：

```text
把 Contract Guard 产出的 memory_card / claim_cards 变成候选；
通过规则优先的 Admission Gate 判断；
只让 admitted candidate 进入 MemoryStore / MemoryView。
```

这一步已经在 v3.3 落地，v3 的 TLC-Memory 不再保留 direct-to-MemoryStore 的架构缺口。

### 下一步：冻结 v4.0，进入 v5

前提：

```text
明确 v3 当前是 lite 版；
基于 v4.0 的 Retry Budget / Read Lease / GC / Preflight Validation 进入比赛验证；
开始 AutoGen / LangGraph Adapter 边界、10 轮稳定压测和 openEuler 复现脚本。
```

风险：

```text
如果 v5 Adapter 绕过 Runtime 内核，系统会退化为“框架外包装”，无法证明跨框架运行时层。
```

因此当前推荐：

```text
进入 v5，开始跨框架 Adapter、10 轮稳定压测、性能报告和 openEuler 复现脚本。
```

## 8. 答辩口径校准

当前版本叙事应改为：

```text
我们采用三线并行加深路线。
v0-v1 建立完整闭环；
v2 进入真实 LLM 和协议可靠化，但 Output Guard 在 v3.2 才补齐工程实现；
v3 聚焦连续任务复用，已实现 ClaimCard / MemoryView / Alias Mapping / Deliverable View；
v3.1 和 v3.2 是 v3 的质量与稳定性补强；
Memory Candidate Admission 已在 v3.3 补齐，v4.0 已补齐成本治理和生命周期 lite，随后进入 v5 的跨框架适配与比赛验证。
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

## 10. v5.12a Framework-Neutral Kernel 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证
小版本：v5.12a
性质：跨框架接入前的内核边界重构
```

与创新方案的对应关系：

| Kernel 能力 | 对应方案 |
|---|---|
| 输出契约校验与修复 | CMJCC Output Contract Guard |
| SHP 交接与通信门控 | CMJCC + SHP |
| StateRef 写入与角色化 Prompt View | SHP-State |
| MemoryContext 与 State-to-Memory Bridge 所有权 | TLC-Memory |
| 任务和会话观测 | 三线联合评测 |

已实现边界：

```text
CollaborationKernel:
  不创建 Agent；
  不决定 Agent 发言顺序；
  不调用 AutoGen、LangGraph 等框架类型；
  负责协作数据面和三线治理原语。

V0Runtime:
  暂时保留当前自研 Agent 调度循环；
  保留 baseline 历史和实验交付选择；
  通过 CollaborationKernel 使用状态、记忆、契约与 SHP。

FrameworkAdapter:
  定义 activate / deactivate / describe_agent 最小协议；
  后续 AutoGenDriver、LangGraphDriver 通过该协议连接 Kernel。
```

尚未实现：

```text
AutoGenDriver；
透明 Patch；
Launcher；
Runtime Service；
AutoGen Studio 托管。
```

因此 v5.12a 只能表述为“跨框架内核边界已经建立”，不能表述为“已经接入 AutoGen”。

## 11. v5.12b Launcher 与 Driver Loader 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证
小版本：v5.12b
性质：透明接入所需的托管启动基础设施
```

已实现：

```text
agentlite run / agentlite start 命令；
受控 subprocess 启动，不使用 shell 命令拼接；
为目标进程生成独立 session_id；
通过 PYTHONPATH 注入专用 sitecustomize；
用户脚本执行前加载 Framework Driver；
Driver 注册表和自定义 module override；
bootstrap_status.json 握手文件；
严格启动失败退出码 78；
检测并拒绝 -S / -I / -E 等禁用注入的 Python 参数；
保留用户原 PYTHONPATH；
清理嵌套启动继承的旧 AgentLite session 环境。
```

AutoGen 当前状态：

```text
AutoGen Bootstrap Driver 已注册；
能够在目标 Python 环境中探测 AutoGen 包；
明确报告 hooks_active=false；
状态为 awaiting_v5.12c_adapter；
尚未拦截 Agent、GroupChat 或 Runtime 消息。
```

因此 v5.12b 可以表述为：

```text
AgentLite 已经能够在不修改用户脚本的情况下，托管启动 Python 进程并在脚本执行前自动加载对应 Driver。
```

不能表述为：

```text
AgentLite 已经透明接管 AutoGen 协作。
```

后者必须等 v5.12c 的 AutoGenDriver 消息生命周期接入完成后才能成立。

## 12. v5.12c AutoGen Driver 校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12c
性质：AutoGen Python 代码端的透明导入钩子与 Kernel 桥接雏形
```

已实现：

```text
AutoGen Driver status=active；
hooks_active=true；
通过 managed import hook 在用户脚本导入 AutoGen 前注册 patch；
支持 autogen_agentchat.agents 的 on_messages / on_messages_stream；
支持 autogen_agentchat.teams 的 run / run_stream；
支持 autogen_core Runtime 类的 send_message / publish_message 观测；
不修改用户传入的 AutoGen message 对象；
不修改 AutoGen 原生返回值；
将可抽取文本输出写入 session-local StatePool 的 artifact_state；
将调用开始、流式片段、调用结束、Kernel 桥接错误写入 autogen_driver/trace.jsonl；
bootstrap_status.json 写入 phase、hook_mode、available_modules、trace_path、state_dir。
```

与创新方案的对应关系：

| v5.12c 能力 | 对应方案模块 |
|---|---|
| Agent/Team/Runtime 生命周期观测 | 跨框架运行时层 |
| 输出以 artifact_state 进入状态池 | SHP-State 非文本/半结构化状态传递 |
| Driver 调用 CollaborationKernel，而不是绕过 Kernel | 三线统一治理边界 |
| 只记录状态引用与摘要，不改写用户消息对象 | 低侵入透明接入 |

当前不能表述为：

```text
已经完成真实 AutoGen 包端到端样例验证；
已经接管 AutoGen Studio 网页端；
已经替换 AutoGen 的底层广播策略；
已经对所有 AutoGen 版本稳定兼容；
已经把 AutoGen 原生消息全部转换为 SHP 控制头。
```

因此 v5.12c 可以表述为：

```text
AgentLite 已能在托管启动的 Python 进程中，于用户脚本导入 AutoGen 前自动安装 Driver 钩子，
并把 AutoGen AgentChat / Core 的关键生命周期事件透明桥接到跨框架 CollaborationKernel。
```

下一步 v5.12d 应使用真实 AutoGen 包运行最小原生样例，验证用户代码不导入 AgentLite 时仍能产生 trace、state_ref 和原生返回值。

## 13. v5.12d-prep 真实 AutoGen 样例准备校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12d-prep
性质：真实 AutoGen 包验证前的样例和校验脚本准备
```

已实现：

```text
pyproject.toml 新增 autogen 可选依赖；
固定 autogen-agentchat==0.7.5；
固定 autogen-core==0.7.5；
新增 examples/autogen_native_roundrobin_smoke.py；
新增 examples/run_autogen_native_smoke.py；
新增 v5.12d-prep 规划和实验记录；
校验脚本可在未安装 AutoGen 时明确提示缺失依赖。
```

不能表述为：

```text
真实 AutoGen 包端到端验证已经完成；
AutoGen Studio 网页端已经接入；
AutoGen 原生 Team 广播策略已经被替换为 SHP handoff。
```

下一步：

```text
经用户确认后安装 autogen 可选依赖；
运行 examples/run_autogen_native_smoke.py；
将输出报告补入 docs/experiments/v5.12d-autogen-native-results.md。
```

## 14. v5.12d 真实 AutoGen 原生样例校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12d
性质：真实 AutoGen 0.7.5 Python 代码端透明接入验证
```

已实现：

```text
新建独立 conda 环境 agentlite-autogen；
安装 autogen-agentchat==0.7.5；
安装 autogen-core==0.7.5；
以 editable 方式安装本项目 autogen 可选依赖；
运行 examples/run_autogen_native_smoke.py；
真实 AutoGen RoundRobinGroupChat smoke passed=true；
目标脚本不导入 AgentLite；
AGENTLITE_AUTOGEN_DRIVER_ACTIVE=true；
trace 记录 autogen_agent_receive / autogen_agent_output；
StatePool 写入 artifact_state；
全量单元测试 98 tests OK。
```

可以表述为：

```text
AgentLite 已经能够在真实 AutoGen 0.7.5 AgentChat 程序中，
于用户脚本不导入 AgentLite 的情况下，
通过托管导入钩子观测 Agent / Team / Runtime 生命周期，
并将输出桥接到 CollaborationKernel 与 StatePool。
```

仍不能表述为：

```text
已经接管 AutoGen Studio 网页端；
已经替换 AutoGen Team 的原生广播策略；
已经支持所有 AutoGen 版本；
已经把 AutoGen 原生消息全部改写为 SHP 控制头。
```

下一步：

```text
修正包版本号为 PEP 440 合法形式；（已在 v5.12e 完成）
补 AutoGen Message Codec；（已在 v5.12e 完成）
增加 TextMessage / ToolCall / HandoffMessage 映射测试；（已在 v5.12e 完成）
再决定是否进入 v5.13 Runtime Service 与 AutoGen Studio 托管。
```

## 15. v5.12e AutoGen Message Codec 增强验证校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12e
性质：真实 AutoGen 消息类型到 AgentLite 内部结构的稳定映射验证
```

已实现：

```text
新增 AutoGenMessageCodec；
TextMessage -> text；
HandoffMessage -> handoff；
ToolCallRequestEvent -> tool_call；
ToolCallExecutionEvent -> tool_result；
ToolCallSummaryMessage -> tool_summary；
Driver trace 写入 decoded_messages；
StatePool audit metadata 写入 autogen_decoded_messages；
修复真实 AutoGen datetime 字段导致 trace JSON 序列化失败的问题；
包版本修正为 0.5.12.post1，避免 0.5.12c0 被 pip 显示为 0.5.12rc0；
新增真实 AutoGen codec smoke；
全量测试 101 tests OK。
```

可以表述为：

```text
AgentLite 已经具备真实 AutoGen 0.7.5 AgentChat/Core 消息对象的稳定解码层，
能够把文本消息、交接消息和工具调用相关消息转换为统一的 trace / StatePool 审计结构。
```

仍不能表述为：

```text
已经接管 AutoGen Studio 网页端；
已经替换 AutoGen Team 原生广播策略；
已经把 AutoGen 原生消息全部改写为 SHP 控制头；
已经支持所有 AutoGen 版本。
```

下一步可选：

```text
v5.12f:
  将 codec 输出接入 SHP handoff envelope 的受控生成；
  保持 AutoGen 原消息不变，只在 AgentLite 数据面生成低开销交接包。

v5.13:
  Runtime Service；
  AutoGen Studio / 网页端托管验证。
```

## 16. v5.12f AutoGen SHP 影子交接包校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12f
性质：真实 AutoGen 调用到 SHP handoff envelope 的旁路生成验证
```

已实现：

```text
新增 agent_runtime/drivers/autogen_shp.py；
AutoGen Driver 在 record_call_end 阶段生成 autogen_shp_handoff_shadow；
只对有语义载荷的 AutoGen 输出写入 StatePool 和 SHP 影子包；
空的 core runtime 生命周期事件只保留 trace，不写 artifact_state；
SHP 影子包复用 CollaborationKernel.build_handoff；
影子包经过 readiness / capability router / communication gate / control budget；
trace 同时保留完整 shadow_envelope 和紧凑 shadow_wire_envelope；
通信 token 口径使用紧凑 wire 包，不把本地审计字段计入跨 agent 通信。
```

与创新方案的对应关系：

| v5.12f 能力 | 对应方案模块 |
|---|---|
| `state_ref` 驱动的 SHP 影子交接包 | SHP-State 非文本状态传递 |
| 有语义载荷才入池 | 状态池分层与低噪声治理 |
| 完整审计包与紧凑 wire 包分离 | 成本口径解耦，避免 cost shifting |
| `CommunicationGateLite` 门控结果写入 trace | Contract / Gate / Budget 治理链 |
| AutoGen 原生返回值不变 | 跨框架透明托管接入 |

真实 AutoGen smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_native_smoke.py --output-dir runs\v5.12f-autogen-shp-shadow-20260628-003

passed: true
autogen_agent_output: 17
state_written: 5
autogen_shp_handoff_shadow: 5
driver_phase_v5_12f: true
shp_shadow_envelope_recorded: true
shp_shadow_gate_recorded: true
全量单元测试：103 tests OK
```

不能表述为：

```text
已经替换 AutoGen Team 的真实广播策略；
已经接管 AutoGen Studio / 网页端；
已经把 SHP wire 包作为 AutoGen agent 间的真实传输内容；
已经完成长内容场景下的 token 降幅验证。
```

下一步建议：

```text
v5.12g:
  构造长内容 AutoGen 原生任务；
  对比 native_text、shadow_wire_envelope、state_ref + Prompt View 的端到端 token 口径；
  继续保持影子验证，不急于替换 AutoGen 原生广播。
```

## 17. v5.12g AutoGen 长内容影子交接包校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12g
性质：长内容 AutoGen 原生任务中的 SHP wire + Prompt View 成本验证
```

已实现：

```text
新增 examples/autogen_long_context_shadow_smoke.py；
新增 examples/run_autogen_long_shadow_smoke.py；
新增 tests/test_autogen_long_shadow_report.py；
AutoGen Driver phase 更新为 v5.12g；
长内容目标脚本仍只导入 AutoGen，不导入 AgentLite；
runner 统计 native_output_text_tokens；
runner 统计紧凑 shadow_wire_tokens；
runner 从 state_written trace 和 payload_ref 还原 artifact_state Prompt View；
runner 统计 shadow_wire_plus_prompt_view_tokens；
runner 明确区分 shadow_audit_envelope_tokens 与跨 agent wire 成本。
```

与创新方案的对应关系：

| v5.12g 能力 | 对应方案模块 |
|---|---|
| 长正文进入 StatePool 冷层 artifact_state | SHP-State 三层状态池 |
| 跨 agent 影子包只携带 `state_ref` 和摘要控制头 | 非文本状态传递 |
| 接收方读取 Prompt View 而不是完整正文 | Prompt View 分层读取 |
| wire 包、Prompt View、本地审计包分开记账 | 端到端成本口径与 cost shifting 防护 |
| AutoGen 原生代码不导入 AgentLite | 透明托管接入 |

真实 AutoGen 长内容 smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_long_shadow_smoke.py --output-dir runs\v5.12g-autogen-long-shadow-20260628-001

passed: true
driver_phase_v5_12g: true
autogen_agent_output: 17
state_written: 5
autogen_shp_handoff_shadow: 5
native_output_text_tokens: 38227
shadow_wire_tokens: 1176
state_prompt_view_tokens: 709
shadow_wire_plus_prompt_view_tokens: 1885
wire_plus_prompt_view_reduction_ratio: 0.950689
全量单元测试：104 tests OK
```

可以表述为：

```text
在长内容 AutoGen 原生任务中，AgentLite 已经能够以影子方式生成基于 state_ref 的 SHP wire 包，
并在 wire 包 + Prompt View 口径下显著低于原生长文本传输成本。
```

不能表述为：

```text
已经替换 AutoGen Team 的真实广播策略；
已经接管 AutoGen Studio / 网页端；
已经把 SHP wire 包作为 AutoGen agent 间的真实传输内容；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估。
```

下一步建议：

```text
v5.12h:
  增加 HandoffMessage / ToolCall 等真实 AutoGen 消息类型的长内容样例；
  验证接收方推断、工具调用结果状态化和 Prompt View 成本；
  仍保持影子模式，不急于进入真实广播替换。
```

## 18. v5.12h AutoGen Handoff / ToolCall 影子状态化校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12h
性质：真实 AutoGen 交接消息和工具调用消息的 SHP 影子状态化验证
```

已实现：

```text
新增 examples/autogen_handoff_tool_shadow_smoke.py；
新增 examples/run_autogen_handoff_tool_shadow_smoke.py；
扩展 tests/test_autogen_long_shadow_report.py；
AutoGen Driver phase 更新为 v5.12h；
目标脚本仍只导入 AutoGen，不导入 AgentLite；
HandoffMessage 的 target 会进入 shadow handoff 接收方推断；
ToolCallRequestEvent / ToolCallExecutionEvent 在 core_runtime receive 阶段写入 autogen_transport_input_state；
工具调用参数和工具调用结果进入 StatePool artifact_state；
ToolCallSummaryMessage 的长摘要进入 SHP shadow 和 Prompt View 成本统计；
runner 统计 decoded_message_kinds、handoff_targets、tool_transport_state_count 和 wire + Prompt View token。
```

与创新方案的对应关系：

| v5.12h 能力 | 对应方案模块 |
|---|---|
| HandoffMessage 目标接收方进入 SHP 影子计划 | SHP-Control 结构化交接 |
| 工具调用参数和结果写入 `artifact_state` | SHP-State 非文本/半结构化状态传递 |
| `autogen_transport_input_state` 记录 core runtime 输入状态 | 跨框架运行时透明接管 |
| `ToolCallSummaryMessage` 进入 Prompt View 成本口径 | 端到端成本口径与 cost shifting 防护 |
| 原生 AutoGen 脚本不导入 AgentLite | SDK/launcher 式透明托管 |

真实 AutoGen handoff/tool-call smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_tool_shadow_smoke.py --output-dir runs\v5.12h-autogen-handoff-tool-20260628-002

passed: true
driver_phase_v5_12h: true
handoff_message_decoded: true
tool_call_request_decoded: true
tool_call_result_decoded: true
tool_call_summary_decoded: true
handoff_target_preserved: true
shadow_plan_uses_handoff_target: true
tool_summary_shadow_state_recorded: true
tool_transport_input_state_recorded: true
autogen_agent_output: 24
autogen_agent_receive: 24
autogen_shp_handoff_shadow: 8
autogen_transport_input_state: 2
state_written: 8
native_output_text_tokens: 36948
shadow_wire_tokens: 1738
state_prompt_view_tokens: 1026
shadow_wire_plus_prompt_view_tokens: 2764
wire_plus_prompt_view_reduction_ratio: 0.925192
全量单元测试：105 tests OK
```

可以表述为：

```text
AgentLite 已经能够在真实 AutoGen Python 代码端透明托管模式下，
对 HandoffMessage、ToolCallRequestEvent、ToolCallExecutionEvent 和 ToolCallSummaryMessage 进行稳定解码，
并把长交接内容、工具调用参数、工具调用结果和工具摘要转化为 StatePool artifact_state 与 SHP 影子交接包。
```

不能表述为：

```text
已经替换 AutoGen Team 的真实广播策略；
已经接管 AutoGen Studio / 网页端；
已经把 SHP wire 包作为 AutoGen agent 间的真实传输内容；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估；
已经覆盖 AutoGen 的所有历史版本和所有消息扩展类型。
```

下一步建议：

```text
v5.12i:
  继续保持影子模式；
  记录 AutoGen Team 原生广播中每个接收方实际收到的消息；
  生成 per-receiver SHP 替代传输计划；
  对比原生广播 token、SHP wire token、Prompt View token；
  验证角色裁剪 Prompt View 是否足够支撑后续真实广播替换。
```

## 19. v5.12i AutoGen Team 广播替换影子计划校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12i
性质：真实替换 AutoGen Team 广播前的 per-receiver SHP 计划验证
```

已实现：

```text
新增 plan_shadow_broadcast；
新增 AutoGenShadowBroadcastPlan；
AutoGen Driver 从真实 RoundRobinGroupChat 实例提取 _participant_names；
AutoGen Driver 在 run_stream 层记录 autogen_team_input_state；
AutoGen Driver 在 run_stream 层记录 autogen_broadcast_replacement_shadow；
广播替换影子计划只记录 run_stream，避免 run 包装 run_stream 导致双计；
新增 examples/autogen_broadcast_shadow_smoke.py；
新增 examples/run_autogen_broadcast_shadow_smoke.py；
扩展 tests/test_autogen_shp.py；
扩展 tests/test_autogen_long_shadow_report.py；
AutoGen Driver phase 更新为 v5.12i。
```

与创新方案的对应关系：

| v5.12i 能力 | 对应方案模块 |
|---|---|
| 每个接收方生成独立 SHP wire 包 | SHP-Control 结构化消息与接收方控制 |
| Team 输入和输出写入 StatePool `artifact_state` | SHP-State 三层状态池 |
| 接收方通过 Prompt View 读取状态 | 分层状态访问与低开销 Prompt View |
| 原生广播 token、wire token、Prompt View token 分开统计 | 端到端成本口径与 cost shifting 防护 |
| 仍不改变 AutoGen 原生返回值 | 跨框架透明托管和可回滚接入 |

真实 AutoGen broadcast shadow smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_broadcast_shadow_smoke.py --output-dir runs\v5.12i-autogen-broadcast-shadow-20260630-002

passed: true
driver_phase_v5_12i: true
autogen_broadcast_replacement_shadow: 2
autogen_team_input_state: 1
state_written: 6
receivers: planner, reviewer, writer
receiver_plan_count: 6
native_full_broadcast_tokens: 42858
shadow_wire_tokens: 1417
prompt_view_tokens: 951
wire_plus_prompt_view_tokens: 2368
token_reduction_ratio: 0.944748
全量单元测试：107 tests OK
```

可以表述为：

```text
AgentLite 已经能够在真实 AutoGen Python 代码端透明托管模式下，
为 Team 广播生成每个接收方独立的 SHP 替代传输计划，
并在影子成本口径中证明 per-receiver wire + Prompt View 明显低于原生全文广播。
```

不能表述为：

```text
已经真实替换 AutoGen Team 的广播策略；
已经改变 AutoGen agent 间的实际传输内容；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估；
已经证明所有 AutoGen Team 类型都可以无修改替换。
```

下一步建议：

```text
v5.12j:
  增加广播替换 feature flag；
  区分 shadow-only、dry-run-rewrite、real-rewrite 三种模式；
  增加 schema / receiver / Prompt View 缺失时的自动回退条件；
  先做 dry-run-rewrite diff，不急于改变真实 AutoGen 消息。
```

## 20. v5.12j AutoGen 广播 dry-run 改写与安全回退校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12j
性质：真实广播替换前的 dry-run 改写、安全开关和 fallback 审计
```

已实现：

```text
新增 AGENTLITE_AUTOGEN_BROADCAST_MODE；
支持 shadow-only / dry-run-rewrite / real-rewrite 三种模式；
默认模式仍为 shadow-only；
runner 默认以 dry-run-rewrite 运行 v5.12j smoke；
AutoGen Driver phase 更新为 v5.12j；
autogen_broadcast_replacement_shadow 新增 broadcast_mode；
autogen_broadcast_replacement_shadow 新增 rewrite_dry_run；
rewrite_dry_run 记录候选改写、替换安全性、回退原因和真实消息是否被改动；
real-rewrite 在 v5.12j 中只接受配置并自动回退；
新增 real-rewrite fallback 单元测试；
完整测试提升到 108 tests OK。
```

与创新方案的对应关系：

| v5.12j 能力 | 对应方案模块 |
|---|---|
| `shadow-only` / `dry-run-rewrite` / `real-rewrite` 模式拆分 | 跨框架透明托管与可回滚接入 |
| 候选 SHP wire 包与原生广播 diff | SHP-Control 结构化交接 |
| Prompt View 缺失、schema 无效、receiver 缺失时回退 | Contract Guard / Readiness / Gate 治理链 |
| token 未降低时不进入改写 | 端到端成本口径与 cost shifting 防护 |
| `real-rewrite` 当前自动回退 | 真实替换前安全阀 |

真实 AutoGen dry-run smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_broadcast_shadow_smoke.py --output-dir runs\v5.12j-autogen-broadcast-dry-run-20260630-001

passed: true
driver_phase_v5_12j: true
broadcast_mode: dry-run-rewrite
autogen_broadcast_replacement_shadow: 2
autogen_team_input_state: 1
rewrite_candidate_count: 2
rewrite_safe_count: 2
fallback_required_count: 0
fallback_reasons: []
native_kept_count: 2
real_message_mutation_count: 0
native_full_broadcast_tokens: 42858
shadow_wire_tokens: 1408
prompt_view_tokens: 942
wire_plus_prompt_view_tokens: 2350
token_reduction_ratio: 0.945168
全量单元测试：108 tests OK
```

可以表述为：

```text
AgentLite 已经具备 AutoGen Team 广播替换前的安全演练能力，
能够在不改变 AutoGen 原生消息的前提下生成候选 SHP 改写结果，
并审计 receiver、state_ref、schema、Prompt View 和 token 收益是否满足替换条件。
```

不能表述为：

```text
已经真实替换 AutoGen Team 的广播策略；
已经改变 AutoGen agent 间的实际传输内容；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估；
已经证明所有 AutoGen Team 类型都可以无修改替换。
```

下一步建议：

```text
v5.12k:
  只针对最简单 TextMessage 广播做 real-rewrite 小范围真实替换；
  只在 receiver、state_ref、Prompt View、schema 全部安全时启用；
  保留自动 fallback 到 AutoGen 原生消息；
  对比 real-rewrite 与 dry-run-rewrite 的 trace 是否一致；
  若 termination_condition 或流式输出异常，立即回退。
```

## 21. v5.12k AutoGen TextMessage 输入层真实改写校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12k
性质：简单 TextMessage 的 agent 输入层真实改写验证
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12k；
AGENTLITE_AUTOGEN_BROADCAST_MODE=real-rewrite 开始启用受限真实改写；
真实改写位置限定为 agentchat_agent.on_messages / on_messages_stream 入参；
仅支持简单 TextMessage 序列；
原始长文本写入 StatePool artifact_state；
agent 实际收到包含 SHP wire 和 Prompt View 的紧凑 TextMessage；
新增 autogen_agent_input_real_rewrite trace 事件；
Team 层广播替换仍安全回退，不直接改 Team 调度；
新增 agent 输入真实改写报告汇总；
完整测试提升到 109 tests OK。
```

与创新方案的对应关系：

| v5.12k 能力 | 对应方案模块 |
|---|---|
| 原始 TextMessage 正文进入 StatePool | SHP-State 三层状态池 |
| agent 实际收到 SHP wire + Prompt View | SHP-Control 结构化消息与分层读取 |
| 改写前检查 `state_ref`、schema、Prompt View、token 收益 | Contract Guard / Readiness / Cost Gate |
| 不支持的消息类型自动回退 | 可回滚透明托管 |
| Team 层仍不改调度，只改 agent 输入层 | 渐进式跨框架接管 |

真实 AutoGen real-rewrite smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_broadcast_shadow_smoke.py --broadcast-mode real-rewrite --output-dir runs\v5.12k-autogen-real-rewrite-20260630-001

passed: true
driver_phase_v5_12k: true
broadcast_mode: real-rewrite
autogen_agent_input_real_rewrite: 3
autogen_broadcast_replacement_shadow: 2
state_written: 9

Team 层：
applied_actions: fallback_keep_native_autogen_broadcast
fallback_reasons: team_level_real_rewrite_not_enabled_in_v5_12k
real_message_mutation_count: 0

Agent 输入层：
agents: planner, reviewer, writer
candidate_count: 3
applied_count: 3
fallback_required_count: 0
real_message_mutation_count: 3
native_input_tokens: 17124
rewritten_input_tokens: 1073
token_delta_native_minus_rewrite: 16051

全量单元测试：109 tests OK
```

可以表述为：

```text
AgentLite 已经能在真实 AutoGen Python 代码端透明托管模式下，
对简单 TextMessage 输入进行小范围真实改写，
把原始长文本转入 StatePool，并让 agent 收到包含 SHP wire 与 Prompt View 的紧凑消息。
```

不能表述为：

```text
已经真实替换 AutoGen Team 层的广播策略；
已经支持 HandoffMessage / ToolCall 的真实改写；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估；
已经证明所有 AutoGen Team 类型都可以无修改替换。
```

下一步建议：

```text
v5.12l:
  增加真实改写三模式对照报告；
  增加 EchoAgent smoke，直接证明 agent 读取到 rewritten content；
  增加 rewrite_attempt / applied / fallback 指标；
  增加 rewritten content 结构校验；
  暂不扩大到 ToolCall / HandoffMessage。
```

## 22. v5.12l AutoGen Rewrite Echo 可观测性校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12l
性质：对 v5.12k 真实改写的可观测性、三模式对照和回归保护
```

已实现：

```text
新增 examples/autogen_rewrite_echo_smoke.py；
新增 examples/run_autogen_rewrite_echo_smoke.py；
AutoGen Driver phase 更新为 v5.12l；
autogen_agent_input_real_rewrite 新增 rewrite_attempt_count / rewrite_applied_count / rewrite_fallback_count；
EchoAgent 直接记录 agent 实际收到的 content；
三模式对照 shadow-only / dry-run-rewrite / real-rewrite；
检查 rewritten content 是否包含 AGENTLITE_REAL_REWRITE v1、state_refs 和 prompt_view；
检查 real-rewrite 是否降低 agent 可见原文 marker 数量、字符数和 token 数。
```

与创新方案的对应关系：

| v5.12l 能力 | 对应方案模块 |
|---|---|
| agent 实际收到 SHP wire + Prompt View | SHP-Control 结构化消息与 SHP-State 分层读取 |
| 原始长文本进入 StatePool，agent 只读紧凑视图 | SHP-State 非文本/半结构化状态传递 |
| rewrite attempt / applied / fallback 指标 | Contract Guard / 可回滚透明托管 |
| shadow / dry-run / real-rewrite 三模式对照 | 跨框架运行时层渐进接管 |
| EchoAgent 直接证明输入层改写生效 | v5 比赛验证与工程审计证据 |

真实 AutoGen rewrite echo smoke 结果：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_rewrite_echo_smoke.py --output-dir runs\v5.12l-autogen-rewrite-echo-20260630-003

passed: true
driver_phase_v5_12l: true
shadow_agent_sees_native_content: true
dry_run_agent_sees_native_content: true
real_rewrite_agent_sees_rewritten_content: true
real_rewrite_contains_state_ref: true
real_rewrite_contains_prompt_view: true
real_rewrite_event_applied: true

shadow_seen_chars: 8372
dry_run_seen_chars: 8372
real_rewrite_seen_chars: 1124
shadow_native_marker_count: 66
dry_run_native_marker_count: 66
real_rewrite_native_marker_count: 1
real_rewrite_native_tokens: 4160
real_rewrite_rewritten_tokens: 338
real_rewrite_token_delta: 3822

全量单元测试：
Ran 110 tests in 2.209s
OK
compileall OK
```

可以表述为：

```text
AgentLite 已经能在真实 AutoGen Python 代码端透明托管模式下，
对简单 TextMessage 的 agent 输入进行真实改写；
EchoAgent 已直接证明 agent 实际读到的是 rewritten content，
而不是仅在 trace 里生成影子改写计划。
```

不能表述为：

```text
已经真实替换 AutoGen Team 层广播策略；
已经支持 HandoffMessage / ToolCall 的真实改写；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估；
已经证明所有 AutoGen Team 类型都可以无修改替换。
```

下一步建议：

```text
v5.12m:
  增加 real-rewrite 失败分桶；
  固化三模式 EchoAgent 报告为后续真实改写扩展的回归基线；
  补充失败样例 smoke，验证自动 fallback 不会破坏 AutoGen 原生执行；
  再考虑 HandoffMessage / ToolCall 的真实改写。
```

## 23. v5.12m AutoGen Rewrite Fallback 分桶校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12m
性质：真实改写失败分桶、安全回退和回归保护
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12m；
autogen_agent_input_real_rewrite 新增 fallback_buckets；
autogen_agent_input_real_rewrite 新增 fallback_bucket_counts；
新增 fallback reason -> fallback bucket 稳定映射；
新增 examples/autogen_rewrite_fallback_smoke.py；
新增 examples/run_autogen_rewrite_fallback_smoke.py；
扩展 summarize_agent_real_rewrite_events；
新增 fallback bucket 单元测试。
```

与创新方案的对应关系：

| v5.12m 能力 | 对应方案模块 |
|---|---|
| 不支持真实改写时保留 AutoGen 原生消息 | 可回滚透明托管 |
| `fallback_reasons` 记录细原因 | Contract Guard / Readiness Guard |
| `fallback_buckets` 记录统计大类 | 交付硬化与稳定性评估 |
| 失败 smoke 验证原生执行不中断 | 跨框架运行时安全接管 |
| 成功 Echo 回归保持通过 | 渐进式真实改写回归基线 |

失败回退 smoke：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_rewrite_fallback_smoke.py --output-dir runs\v5.12m-autogen-rewrite-fallback-20260630-001

passed: true
driver_phase_v5_12m: true
agent_saw_native_handoff: true
agent_did_not_see_rewrite_marker: true
fallback_event_recorded: true
rewrite_not_applied: true
fallback_count_recorded: true
unsupported_reason_recorded: true
unsupported_bucket_recorded: true
native_message_not_mutated: true

event_count: 1
attempt_count: 1
applied_count: 0
fallback_count: 1
fallback_reasons: ['non_text_message_present']
fallback_buckets: ['unsupported_message_type']
fallback_bucket_counts: {'unsupported_message_type': 1}
real_message_mutation_count: 0
seen_message_type: HandoffMessage
seen_native_marker_count: 17
```

成功改写回归：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_rewrite_echo_smoke.py --output-dir runs\v5.12m-autogen-rewrite-echo-20260630-001

passed: true
all_driver_phase_v5_12m: true
real_rewrite_agent_sees_rewritten_content: true
real_rewrite_contains_state_ref: true
real_rewrite_contains_prompt_view: true
real_rewrite_event_applied: true
real_rewrite_reduces_tokens: true

real_rewrite_native_tokens: 4160
real_rewrite_rewritten_tokens: 336
real_rewrite_token_delta: 3824
```

全量测试：

```text
Ran 111 tests in 2.203s
OK
compileall OK
```

可以表述为：

```text
AgentLite 已经具备真实改写失败分桶和安全回退证据；
当消息类型当前不支持真实改写时，系统不会强行改写 AutoGen 原生消息，
而是记录 fallback reason / fallback bucket 并保持原生执行继续。
```

不能表述为：

```text
已经支持 HandoffMessage / ToolCall 的真实改写；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估。
```

下一步建议：

```text
v5.12n:
  补更多失败样例；
  覆盖 empty_messages、empty_text_payload、token_not_reduced 等分桶；
  继续保持不扩展真实改写范围；
  等失败回退证据稳定后，再考虑 HandoffMessage / ToolCall 真实改写。
```

## 24. v5.12n AutoGen Rewrite Fallback Matrix 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12n
性质：真实改写失败矩阵和安全回退证据扩展
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12n；
新增 examples/autogen_rewrite_fallback_matrix_smoke.py；
新增 examples/run_autogen_rewrite_fallback_matrix_smoke.py；
新增 _text_message_content，空 TextMessage 在进入状态池前归入 empty_text_payload；
失败矩阵覆盖 empty_messages、empty_text_payload、token_not_reduced、non_text_message_present；
成功改写 Echo 回归仍保持通过。
```

与创新方案的对应关系：

| v5.12n 能力 | 对应方案模块 |
|---|---|
| 空输入回退 | Contract Guard 输入契约守卫 |
| 空文本回退 | Provider/Contract 内容空值守卫 |
| 短文本成本门控失败回退 | CSCC / 成本门控 lite |
| 非 TextMessage 回退 | 可回滚透明托管 |
| 成功改写回归 | 渐进式真实改写回归基线 |

失败矩阵 smoke：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_rewrite_fallback_matrix_smoke.py --output-dir runs\v5.12n-autogen-rewrite-fallback-matrix-20260630-002

passed: true
driver_phase_v5_12n: true
four_cases_recorded: true
four_fallback_events_recorded: true
no_rewrite_applied: true
fallback_count_is_four: true
all_expected_reasons_recorded: true
all_expected_buckets_recorded: true
short_text_native_preserved: true
handoff_native_preserved: true
native_messages_not_mutated: true

event_count: 4
attempt_count: 4
applied_count: 0
fallback_count: 4
fallback_reasons:
  ['empty_messages', 'empty_text_payload', 'non_text_message_present', 'token_not_reduced']
fallback_buckets:
  ['cost_gate_failed', 'empty_payload', 'input_contract_empty', 'unsupported_message_type']
fallback_bucket_counts:
  {'cost_gate_failed': 1, 'empty_payload': 1, 'input_contract_empty': 1, 'unsupported_message_type': 1}
real_message_mutation_count: 0
```

成功改写回归：

```text
命令：
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_rewrite_echo_smoke.py --output-dir runs\v5.12n-autogen-rewrite-echo-20260630-001

passed: true
all_driver_phase_v5_12n: true
real_rewrite_agent_sees_rewritten_content: true
real_rewrite_contains_state_ref: true
real_rewrite_contains_prompt_view: true
real_rewrite_event_applied: true
real_rewrite_reduces_tokens: true

real_rewrite_native_tokens: 4160
real_rewrite_rewritten_tokens: 341
real_rewrite_token_delta: 3819
```

全量测试：

```text
Ran 111 tests in 2.167s
OK
compileall OK
```

可以表述为：

```text
AgentLite 已经把真实改写失败回退从单点样例扩展为矩阵验证；
当前失败场景会记录 reason 和 bucket，并保持 AutoGen 原生消息不被修改。
```

不能表述为：

```text
已经支持 HandoffMessage / ToolCall 的真实改写；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估。
```

下一步建议：

```text
进入 HandoffMessage / ToolCall 真实改写设计前，应先定义最小安全条件：
  何时允许改写；
  如何保留 target / tool_call_id / result lineage；
  如何生成接收方 Prompt View；
  如何验证改写后仍不破坏 AutoGen 原生语义。
```

## 25. v5.12o AutoGen Handoff / ToolCall Rewrite Guard 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12o
性质：Handoff / ToolCall 真实改写前的最小安全守卫与审计证据
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12o；
新增 autogen_non_text_real_rewrite_guard.v1 安全审计结构；
非 TextMessage 回退时保留 non_text_message_present，同时细分 Handoff / ToolCall 安全原因；
新增 handoff_control_guard 与 tool_lineage_guard 分桶；
新增 examples/autogen_handoff_tool_rewrite_guard_smoke.py；
新增 examples/run_autogen_handoff_tool_rewrite_guard_smoke.py；
新增 summarize_rewrite_safety 单元测试；
TextMessage 成功真实改写回归继续通过。
```

与创新方案的对应关系：

| v5.12o 能力 | 对应方案模块 |
|---|---|
| Handoff target 保留审计 | SHP-Control 结构化交接与可回滚托管 |
| ToolCall / Result call_id 链路审计 | 非文本状态传递与工具调用 lineage 保护 |
| `safe_to_mutate=false` 显式记录 | Contract Guard / 透明降级 |
| 保持 AutoGen 原生消息不变 | 跨框架运行时层的低侵入接管 |
| TextMessage 改写回归 | 渐进式真实改写闭环 |

Guard smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_tool_rewrite_guard_smoke.py --output-dir runs\v5.12o-autogen-handoff-tool-guard-20260630-001

passed: true
driver_phase_v5_12o: true
two_cases_recorded: true
two_guard_events_recorded: true
no_rewrite_applied: true
fallback_count_is_two: true
native_messages_not_mutated: true
handoff_native_preserved: true
tool_summary_native_preserved: true
safety_contract_recorded: true
safety_requires_native_preservation: true
safety_marks_mutation_unsafe: true
handoff_target_audited: true
tool_lineage_audited: true

fallback_reasons:
  ['handoff_rewrite_requires_target_preservation',
   'non_text_message_present',
   'tool_rewrite_requires_call_lineage',
   'tool_rewrite_requires_result_lineage']

fallback_buckets:
  ['handoff_control_guard',
   'tool_lineage_guard',
   'unsupported_message_type']

required_native_fields:
  ['content',
   'results.call_id',
   'results.is_error',
   'results.name',
   'source',
   'target',
   'tool_calls.id',
   'tool_calls.name']

real_message_mutation_count: 0
```

回归验证：

```text
fallback matrix:
  passed: true
  fallback_count: 4
  applied_count: 0
  real_message_mutation_count: 0

TextMessage echo:
  passed: true
  real_rewrite_native_tokens: 2147
  real_rewrite_rewritten_tokens: 299
  real_rewrite_token_delta: 1848

tests:
  Ran 112 tests in 2.138s
  OK
  compileall OK
```

可以表述为：

```text
AgentLite 已经能在 real-rewrite 模式下识别 Handoff / ToolCall 这类带控制语义的 AutoGen 消息，
并在未满足 typed rewrite 条件时保持原生消息不变，同时记录 target、tool_call_id、result lineage 等安全字段。
这证明系统不是盲目压缩消息，而是在低开销通信前保留框架语义边界。
```

不能表述为：

```text
已经支持 HandoffMessage / ToolCall 的真实改写；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端；
已经完成真实 LLM AutoGen 任务的质量、延迟、token 综合评估。
```

下一步建议：

```text
v5.12p 可进入 typed rewrite candidate 设计。
建议先选择 HandoffMessage，因为它的关键字段少于 ToolCallSummaryMessage：
  source；
  target；
  content；
  可选 context。

ToolCallSummaryMessage 需要同时保护 tool_calls.id 与 results.call_id 的 lineage，
适合在 HandoffMessage typed rewrite 通过后再做。
```

## 26. v5.12p AutoGen Handoff Typed Rewrite Candidate 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12p
性质：HandoffMessage 真实改写前的类型化候选验证
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12p；
HandoffMessage 非文本分支新增 typed_rewrite_candidate 审计字段；
新增 autogen_handoff_typed_rewrite_candidate.v1 合约；
candidate 写入 StatePool，生成 SHP wire 与 receiver Prompt View；
candidate 克隆 HandoffMessage 并验证 message_type/source/target/id/metadata/context/content；
当前 mutation_applied=false，只做候选验证，不修改 AutoGen 原生 HandoffMessage；
TextMessage 真实改写回归继续通过。
```

与创新方案的对应关系：

| v5.12p 能力 | 对应方案模块 |
|---|---|
| Handoff content 写入 StatePool | SHP-State / 非文本状态传递 |
| SHP wire + Prompt View candidate | SHP-Control 结构化交接 |
| source / target / id / metadata / context 检查 | Contract Guard / 跨框架语义保护 |
| `mutation_applied=false` | 可回滚透明托管 |
| token reduced 检查 | CSCC / 成本门控 |

Handoff candidate smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_tool_rewrite_guard_smoke.py --output-dir runs\v5.12p-autogen-handoff-typed-candidate-20260630-001

passed: true
driver_phase_v5_12p: true
handoff_typed_candidate_recorded: true
handoff_typed_candidate_contract_recorded: true
handoff_typed_candidate_semantic_safe: true
handoff_typed_candidate_not_mutated: true
handoff_typed_candidate_preserves_control_fields: true
handoff_typed_candidate_reduces_tokens: true

contract: autogen_handoff_typed_rewrite_candidate.v1
source: router
target: guard
native_id: handoff_guard_1
metadata_keys: ['route']
context_count: 0
candidate_safe_count: 1
mutation_applied_count: 0
native_input_tokens: 618
candidate_input_tokens: 304
token_delta_native_minus_candidate: 314
```

语义保持检查：

```text
message_type_preserved: true
source_preserved: true
target_preserved: true
id_preserved: true
metadata_preserved: true
context_preserved: true
content_replaced_only: true
state_ref_available: true
schema_valid: true
prompt_view_available: true
token_reduced: true
```

回归验证：

```text
TextMessage echo:
  passed: true
  real_rewrite_native_tokens: 2147
  real_rewrite_rewritten_tokens: 300
  real_rewrite_token_delta: 1847

fallback matrix:
  passed: true
  fallback_count: 4
  applied_count: 0
  real_message_mutation_count: 0
```

可以表述为：

```text
AgentLite 已经能为 AutoGen HandoffMessage 生成 typed rewrite candidate，
并证明该候选在保持 source、target、id、metadata、context 等控制字段的同时可以降低传递 token。
```

不能表述为：

```text
已经真实改写 HandoffMessage；
已经支持 ToolCallSummaryMessage 真实改写；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端。
```

下一步建议：

```text
v5.12q 可以增加显式 Handoff rewrite 开关：
  AGENTLITE_AUTOGEN_HANDOFF_REWRITE=1

默认关闭。
只有当 candidate_safe=true、token_reduced=true、schema_valid=true、prompt_view_available=true 时，
才允许真实替换 HandoffMessage.content。
```

## 27. v5.12q AutoGen Handoff Rewrite Switch 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12q
性质：HandoffMessage content 层受控真实改写
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12q；
新增 AGENTLITE_AUTOGEN_HANDOFF_REWRITE；
driver_details 暴露 handoff_rewrite_enabled 与 handoff_rewrite_env；
默认关闭时只生成 typed_rewrite_candidate，不修改原生 HandoffMessage；
开关开启时，candidate 必须满足语义、schema、Prompt View 和 token 降低门控；
真实改写时只替换 HandoffMessage.content；
source、target、id、metadata、context 保持不变；
ToolCallSummaryMessage 仍保持回退。
```

与创新方案的对应关系：

| v5.12q 能力 | 对应方案模块 |
|---|---|
| 显式开关控制真实改写 | 透明托管与可回滚接管 |
| Handoff content -> StatePool + SHP wire | SHP-State / SHP-Control |
| `source/target/id/metadata/context` 保留 | Contract Guard / 框架语义保护 |
| `candidate_safe/token_reduced/schema_valid/prompt_view_available` 联合门控 | CSCC / 成本与可靠性守卫 |
| ToolCall 继续回退 | 渐进式跨框架适配边界 |

关闭模式 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_tool_rewrite_guard_smoke.py --handoff-rewrite off --output-dir runs\v5.12q-autogen-handoff-rewrite-off-20260630-003

passed: true
handoff_rewrite_enabled: false
candidate_safe_count: 1
mutation_applied_count: 0
applied_count: 0
fallback_count: 2
real_message_mutation_count: 0
native_input_tokens: 618
candidate_input_tokens: 316
token_delta_native_minus_candidate: 302
```

开启模式 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_tool_rewrite_guard_smoke.py --handoff-rewrite on --output-dir runs\v5.12q-autogen-handoff-rewrite-on-20260630-003

passed: true
handoff_rewrite_enabled: true
candidate_safe_count: 1
mutation_applied_count: 1
applied_count: 1
fallback_count: 1
real_message_mutation_count: 1
native_input_tokens: 618
candidate_input_tokens: 300
token_delta_native_minus_candidate: 318
```

语义保持检查：

```text
message_type_preserved: true
source_preserved: true
target_preserved: true
id_preserved: true
metadata_preserved: true
context_preserved: true
content_replaced_only: true
state_ref_available: true
schema_valid: true
prompt_view_available: true
token_reduced: true
```

回归验证：

```text
TextMessage echo:
  passed: true
  real_rewrite_native_tokens: 2147
  real_rewrite_rewritten_tokens: 309
  real_rewrite_token_delta: 1838

fallback matrix:
  passed: true
  fallback_count: 4
  applied_count: 0
  real_message_mutation_count: 0
```

可以表述为：

```text
AgentLite 已经支持受控的 AutoGen HandoffMessage content 真实改写。
它默认关闭，只有显式开关打开且所有安全门控通过时，才替换 HandoffMessage.content。
```

不能表述为：

```text
已经支持 ToolCallSummaryMessage 真实改写；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端；
所有 Handoff 场景都能安全真实改写。
```

下一步建议：

```text
v5.12r 应进入 Handoff rewrite 压测矩阵：
  多 HandoffMessage；
  非空 context；
  token_not_reduced；
  prompt_view_missing；
  schema_invalid；
  开关关闭必须保持原生消息。

矩阵稳定后，再进入 ToolCallSummaryMessage typed rewrite candidate。
```

## 28. v5.12r AutoGen Handoff Rewrite Matrix 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12r
性质：HandoffMessage content 层真实改写的压测矩阵与边界证明
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12r；
新增 autogen_handoff_rewrite_matrix_smoke.py；
新增 run_autogen_handoff_rewrite_matrix_smoke.py；
矩阵覆盖开关关闭、单条长 Handoff、短内容成本门、多 Handoff、带 context Handoff；
开关关闭时只记录候选，不修改原生消息；
开关开启时只改写单条、语义安全、schema 有效、Prompt View 可用且 token_reduced=true 的 Handoff；
短内容 token 不降低时保留原生；
多 Handoff 输入暂不生成单条 typed candidate，保留原生；
带 context 的 Handoff 在 context_preserved=true 后允许改写。
```

与创新方案的对应关系：

| v5.12r 能力 | 对应方案模块 |
|---|---|
| 开关关闭强制原生保留 | 透明托管与可回滚接管 |
| 单条长 Handoff 改写 | SHP-State / SHP-Control |
| 短内容 token_reduced=false 回退 | CSCC / 成本门控 |
| 多 Handoff 保留原生 | Contract Guard / 框架语义保护 |
| context_preserved=true 后改写 | Contract Guard / 上下文语义保护 |

主矩阵 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_handoff_rewrite_matrix_smoke.py --output-dir runs\v5.12r-autogen-handoff-matrix-20260630-001

passed: true
off_applied_count: 0
on_applied_count: 2
off_fallback_count: 1
on_fallback_count: 2
on_candidate_event_count: 3
on_candidate_mutation_applied_count: 2
on_candidate_token_delta: 656
```

关键结论：

```text
off_switch_keeps_native_handoff: true
single_long_rewritten_content_preserves_control: true
short_text_fails_cost_gate_and_stays_native: true
multi_handoff_stays_native: true
context_handoff_rewritten_and_context_preserved: true
candidate_matrix_records_token_gate_failure: true
```

回归验证：

```text
Handoff/ToolCall guard off: passed=true
Handoff/ToolCall guard on: passed=true
TextMessage echo: passed=true
Fallback matrix: passed=true
unittest: Ran 113 tests OK
compileall: passed
```

可以表述为：

```text
AgentLite 已经通过矩阵证明：AutoGen HandoffMessage content 层真实改写只会在开关开启、语义字段保留、Prompt View 可用、schema 有效且 token 确实降低时发生；否则保持原生 AutoGen 消息。
```

不能表述为：

```text
已经支持 ToolCallSummaryMessage 真实改写；
已经接管 AutoGen Studio / 网页端；
已经真实替换 AutoGen Team 层广播策略；
所有 Handoff 场景都可以真实改写。
```

下一步建议：

```text
进入 ToolCallSummaryMessage typed rewrite candidate。
需要优先验证 tool_calls.id、tool_calls.name、results.call_id、results.name、is_error 等 lineage 字段是否能在 content 改写后完整保留。
```

## 29. v5.12s AutoGen ToolCallSummary Typed Rewrite Candidate 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12s
性质：ToolCallSummaryMessage 真实改写前的类型化候选验证
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12s；
非文本分支在 Handoff candidate 之后新增 ToolCallSummary candidate；
新增 autogen_tool_summary_typed_rewrite_candidate.v1 合约；
新增 AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1 候选内容标记；
ToolCallSummary 长正文写入 StatePool；
生成 SHP wire + receiver Prompt View；
克隆 ToolCallSummaryMessage 并验证 source/id/metadata/content；
验证 tool_calls 与 results 完整保留；
验证 tool_calls.id/name 与 results.call_id/name/is_error 链路字段；
当前仍不真实 mutation，agent 收到的仍是原生 ToolCallSummaryMessage。
```

与创新方案的对应关系：

| v5.12s 能力 | 对应方案模块 |
|---|---|
| ToolCallSummary 长正文 -> StatePool | SHP-State / 非文本状态传递 |
| SHP wire + Prompt View candidate | SHP-Control 结构化交接 |
| `tool_calls.id/name` 保留 | Contract Guard / 工具调用链路保护 |
| `results.call_id/name/is_error` 保留 | Contract Guard / 工具结果链路保护 |
| token_reduced 检查 | CSCC / 成本门控 |
| 不真实 mutation | 透明托管与可回滚接管 |

主 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_tool_summary_candidate_smoke.py --output-dir runs\v5.12s-autogen-tool-summary-candidate-20260630-001

passed: true
event_count: 1
applied_count: 0
fallback_count: 1
real_message_mutation_count: 0
native_input_tokens: 1082
candidate_input_tokens: 333
token_delta_native_minus_candidate: 749
candidate_safe_count: 1
mutation_applied_count: 0
```

关键结论：

```text
native_tool_summary_preserved_for_agent: true
tool_lineage_preserved_for_agent: true
tool_lineage_recorded_in_safety: true
tool_lineage_recorded_in_candidate: true
candidate_preserves_required_fields: true
candidate_reduces_tokens: true
```

回归验证：

```text
Handoff matrix: passed=true
Handoff/ToolCall guard on: passed=true
TextMessage echo: passed=true
Fallback matrix: passed=true
unittest: Ran 113 tests OK
compileall: passed
```

可以表述为：

```text
AgentLite 已经能为 AutoGen ToolCallSummaryMessage 生成类型化改写候选，在不改变原生消息的前提下证明：长正文可以迁移到 StatePool，且工具调用请求与执行结果链路仍完整可审计。
```

不能表述为：

```text
已经真实改写 ToolCallSummaryMessage；
已经真实替换 ToolCallSummaryMessage.content；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端。
```

下一步建议：

```text
进入 ToolCallSummary rewrite matrix。
优先覆盖 call_id 不匹配、多工具调用、多工具结果、短内容不降成本、is_error=true 错误结果保留等边界。
矩阵稳定后，再考虑 ToolCallSummaryMessage content 层真实改写开关。
```

## 30. v5.12t AutoGen ToolCallSummary Rewrite Matrix 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12t
性质：ToolCallSummaryMessage 类型化改写候选的边界矩阵验证
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12t；
新增 autogen_tool_summary_matrix_smoke.py；
新增 run_autogen_tool_summary_matrix_smoke.py；
矩阵覆盖正常 call_id 对齐、call_id 不匹配、多工具调用、多结果、短内容不降成本、is_error=true；
所有场景仍保持原生 ToolCallSummaryMessage 传给 AutoGen agent；
aligned_single 生成 safe candidate；
mismatched_call_id 生成 unsafe candidate，tool_result_lineage_complete=false；
multi_tool_complete 验证多工具调用与多结果完整保留；
short_cost_gate 验证 token_reduced=false 时不能进入真实改写；
error_result_preserved 验证 is_error=true 错误状态不会丢失。
```

与创新方案的对应关系：

| v5.12t 能力 | 对应方案模块 |
|---|---|
| ToolCallSummary 矩阵化边界验证 | Contract Guard / 协议可靠性 |
| `call_id` 不匹配拦截 | 工具调用 lineage 保护 |
| 多工具调用与多结果保留 | 非文本状态传递的语义守卫 |
| 短内容 token_reduced=false 回退 | CSCC / 成本门控 |
| `is_error=true` 保留 | 工具结果错误语义保护 |
| 所有场景不真实 mutation | 透明托管与可回滚接管 |

主矩阵 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_tool_summary_matrix_smoke.py --output-dir runs\v5.12t-autogen-tool-summary-matrix-20260630-001

passed: true
event_count: 5
applied_count: 0
fallback_count: 5
real_message_mutation_count: 0
candidate_safe_count: 4
mutation_applied_count: 0
native_input_tokens: 4514
candidate_input_tokens: 1665
token_delta_native_minus_candidate: 2849
failing_semantic_checks: ['token_reduced', 'tool_result_lineage_complete']
```

关键结论：

```text
aligned_single_candidate_safe: true
mismatched_call_id_candidate_unsafe: true
multi_tool_candidate_preserves_all_lineage: true
short_cost_gate_fails_token_reduced: true
error_result_preserves_error_flag: true
all_agents_receive_native_tool_summary: true
```

回归验证：

```text
ToolCallSummary candidate: passed=true
Handoff matrix: passed=true
Handoff/ToolCall guard on: passed=true
TextMessage echo: passed=true
Fallback matrix: passed=true
unittest: Ran 114 tests OK
compileall: passed
```

可以表述为：

```text
AgentLite 已经通过矩阵证明：AutoGen ToolCallSummaryMessage 的类型化改写候选可以区分安全链路、错误链路和不划算链路；系统不会为了降低 token 而破坏工具调用请求与工具结果的对应关系。
```

不能表述为：

```text
已经真实改写 ToolCallSummaryMessage；
已经真实替换 ToolCallSummaryMessage.content；
已经真实替换 AutoGen Team 层广播策略；
已经接管 AutoGen Studio / 网页端。
```

下一步建议：

```text
进入 v5.12u ToolCallSummary rewrite switch。
默认关闭；
显式开关开启；
仅在 candidate_safe=true、token_reduced=true、tool_result_lineage_complete=true、schema_valid=true、prompt_view_available=true 时真实替换 ToolCallSummaryMessage.content。
```

## 31. v5.12u AutoGen ToolCallSummary Rewrite Switch 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12u
性质：ToolCallSummaryMessage content 层真实改写开关
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12u；
新增 AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE 开关；
新增 autogen_tool_summary_rewrite_switch_smoke.py；
新增 run_autogen_tool_summary_rewrite_switch_smoke.py；
关闭开关时只生成 autogen_tool_summary_typed_rewrite_candidate.v1，不改原生消息；
开启开关时，仅在全部语义安全条件通过后替换 ToolCallSummaryMessage.content；
替换范围限定为 content 字段；
ToolCallSummaryMessage 的 id/source/metadata/tool_calls/results 继续保持 AutoGen 原生对象语义；
trace 同时记录 real rewrite audit、safety guard、typed candidate 三类证据。
```

与创新方案的对应关系：

| v5.12u 能力 | 对应方案模块 |
|---|---|
| ToolCallSummary 长正文 -> StatePool | SHP-State / 非文本状态传递 |
| content-only 真实替换 | SHP-Control / 透明托管接管 |
| `tool_calls/results` 原生保留 | Contract Guard / 工具调用链路保护 |
| `AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE` 开关 | 可回滚接管 / 灰度启用 |
| `token_reduced=true` 才替换 | CSCC / 成本感知门控 |
| schema 和 Prompt View 必须通过 | 输出协议守卫 / Prompt View 生成 |

主 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_tool_summary_rewrite_switch_smoke.py --output-dir .\runs\v5.12u-autogen-tool-summary-rewrite-switch-20260630-002

passed: true
off_applied_count: 0
on_applied_count: 1
off_fallback_count: 1
on_fallback_count: 0
off_candidate_mutation_applied_count: 0
on_candidate_mutation_applied_count: 1
native_input_tokens: 1371
rewritten_input_tokens: 327
token_delta_native_minus_rewrite: 1044
```

关键结论：

```text
off_mode_keeps_native_tool_summary: true
off_mode_candidate_safe_not_mutated: true
on_mode_rewrites_tool_summary_content: true
on_mode_preserves_tool_lineage_for_agent: true
on_mode_candidate_mutated_and_safe: true
switch_preserves_candidate_contract_in_both_modes: true
```

回归验证：

```text
ToolCallSummary switch: passed=true
ToolCallSummary matrix: passed=true
ToolCallSummary candidate: passed=true
Handoff matrix: passed=true
Handoff/ToolCall guard on: passed=true
TextMessage echo: passed=true
Fallback matrix: passed=true
Native AutoGen smoke: passed=true
unittest: Ran 114 tests OK
compileall: passed
```

综合 AutoGen-only 用户脚本验证：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_integrated_rewrite_smoke.py --output-dir .\runs\v5.12u-autogen-integrated-rewrite-20260630-002

passed: true
user_script_does_not_import_agentlite: true
agentlite_active_in_user_process: true
text_message_rewritten: true
handoff_message_rewritten: true
tool_summary_message_rewritten: true
tool_summary_lineage_preserved: true
applied_count: 3
fallback_count: 0
real_message_mutation_count: 3
native_input_tokens: 3930
rewritten_input_tokens: 942
token_delta_native_minus_rewrite: 2988
```

综合验证结论：

```text
用户脚本只导入 AutoGen，不显式导入 AgentLite；
托管启动后，AgentLite 通过底层 patch 接管 agent 输入层；
TextMessage、HandoffMessage、ToolCallSummaryMessage 三类消息的 content 均可替换为 SHP wire + Prompt View；
Handoff 的 source/target/id/metadata/context 保留；
ToolCallSummary 的 tool_calls/results 链路保留。
```

可以表述为：

```text
AgentLite 已经可以在托管启动的 AutoGen Python 代码端，对 AutoGen agent 输入层进行受控真实接管：用户脚本不需要显式导入 AgentLite；在 real-rewrite 模式及对应安全开关开启后，TextMessage、HandoffMessage、ToolCallSummaryMessage 的 content 可被替换为 SHP wire + Prompt View，把长正文移入 StatePool，同时保留 Handoff 控制字段和 ToolCallSummary 的原生 tool_calls/results 链路。
```

不能表述为：

```text
已经接管 AutoGen Studio / 网页端；
已经真实替换 AutoGen Team 层原生广播策略；
已经覆盖所有 AutoGen 消息类型的真实替换；
已经提供可视化监控网站。
```

下一步建议：

```text
进入综合原生替换验证。
构造一个用户脚本只导入 AutoGen，不导入 AgentLite；
通过 agentlite run --framework autogen -- python app.py 启动；
在同一条链路中同时覆盖 TextMessage、HandoffMessage、ToolCallSummaryMessage；
验证 TextMessage 和安全非文本消息真实替换，短内容/链路异常等场景安全回退；
形成“代码端无显式感知，底层消息内容由 AgentLite 接管”的端到端证据。
```

## 32. v5.12v AutoGen Team Rewrite Switch 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12v
性质：AutoGen Team 入口层真实改写开关
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12v；
新增 AGENTLITE_AUTOGEN_TEAM_REWRITE 开关；
新增 autogen_team_rewrite_smoke.py；
新增 run_autogen_team_rewrite_smoke.py；
在 AGENTLITE_AUTOGEN_BROADCAST_MODE=real-rewrite 且 AGENTLITE_AUTOGEN_TEAM_REWRITE=1 时，
对 RoundRobinGroupChat.run_stream(task=...) 的 task: str 做 Team 入口真实替换；
原始 task 正文写入 StatePool；
传入 AutoGen Team 的 task 替换为 AGENTLITE_TEAM_REAL_REWRITE v1；
替换内容包含 broadcast_manifest、state_refs、receiver_wires 和 receiver_prompt_views；
新增 autogen_team_input_real_rewrite trace 事件；
保留旧的 autogen_broadcast_replacement_shadow 事件，并在 Team 开关开启时记录 real_rewrite_team_task_broadcast。
```

与创新方案的对应关系：

| v5.12v 能力 | 对应方案模块 |
|---|---|
| Team task 正文 -> StatePool | SHP-State / 非文本状态传递 |
| Team 入口 content 替换 | 跨框架透明运行时接管 |
| broadcast_manifest | SHP-Control / 结构化交接 |
| receiver_prompt_views | Prompt View / 接收方视图裁剪 |
| `AGENTLITE_AUTOGEN_TEAM_REWRITE` | 可回滚接管 / 灰度启用 |
| task token 和 broadcast token 双口径记录 | CSCC / 端到端成本口径 |

主 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_team_rewrite_smoke.py --output-dir .\runs\v5.12v-autogen-team-rewrite-20260701-001

passed: true
participants: ['planner', 'reviewer', 'writer']
applied_count: 1
fallback_count: 0
real_message_mutation_count: 1
native_task_tokens: 1596
rewritten_task_tokens: 1044
token_delta_native_task_minus_rewrite: 552
native_full_broadcast_tokens: 4788
wire_plus_prompt_view_tokens: 899
token_delta_native_broadcast_minus_rewrite: 3889
```

AutoGen stream 侧证据：

```text
type: TextMessage
source: user
contains_team_rewrite_marker: true
contains_native_marker: false
native_marker_count: 0
contains_broadcast_manifest: true
contains_receiver_prompt_views: true
```

回归验证：

```text
Team rewrite smoke: passed=true
Broadcast real-rewrite smoke with Team switch off: passed=true
Integrated AutoGen-only rewrite smoke: passed=true
ToolSummary rewrite switch: passed=true
Fallback matrix: passed=true
Handoff matrix: passed=true
ToolSummary matrix: passed=true
Native AutoGen smoke: passed=true
Rewrite echo smoke: passed=true
unittest: Ran 114 tests OK
compileall: passed
git diff --check: only LF/CRLF warnings
```

可以表述为：

```text
AgentLite 已经可以在 AutoGen Python 代码端通过托管启动接管 Team 入口层：
当用户调用 RoundRobinGroupChat.run_stream(task=长文本) 时，
AgentLite 可在 AutoGen 原生分发前将长任务迁移到 StatePool，
并把传入 Team 的任务替换为紧凑的 SHP broadcast manifest + Prompt Views。
```

不能表述为：

```text
已经接管 AutoGen Studio / 网页端；
已经重写 AutoGen 所有 Team 内部私有消息队列；
已经支持所有 task 类型的 Team 层替换；
已经覆盖所有 AutoGen Team 实现。
```

下一步建议：

```text
进入 Team rewrite matrix。
覆盖 task=None、短 task、BaseChatMessage task、消息列表 task、缺参与者、开关关闭、run 间接调用 run_stream 等边界；
证明 Team 入口接管不会误伤复杂任务，也不会绕过安全回退。
```

## 33. v5.12w AutoGen Team Rewrite Matrix 校准

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12w
性质：AutoGen Team 入口层真实改写边界矩阵
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12w；
新增 autogen_team_rewrite_matrix_smoke.py；
新增 run_autogen_team_rewrite_matrix_smoke.py；
矩阵分 off/on 两组启动；
off 组验证 AGENTLITE_AUTOGEN_TEAM_REWRITE=0 时保留原生 task；
on 组验证长 task: str 可真实替换；
on 组验证短 task 因成本门控安全回退；
on 组验证 TextMessage task 和 list task 因暂不支持而安全回退；
on 组验证 task=None 因缺少任务参数而安全回退；
on 组验证 RoundRobinGroupChat.run(...) 间接路径仍可触发 run_stream 改写。
```

与创新方案的对应关系：

| v5.12w 能力 | 对应方案模块 |
|---|---|
| off/on 显式矩阵 | 可回滚接管 / 灰度启用 |
| 长 task 改写 | SHP-State + SHP-Control |
| 短 task 回退 | CSCC / 成本感知门控 |
| 复杂 task 类型回退 | Contract Guard / 输入契约保护 |
| task=None 回退 | Provider/Runtime 输入守卫 |
| run 间接路径验证 | 跨框架透明托管稳定性 |

主 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_team_rewrite_matrix_smoke.py --output-dir .\runs\v5.12w-autogen-team-matrix-20260701-002

passed: true
off_applied_count: 0
off_fallback_count: 1
on_applied_count: 2
on_fallback_count: 4
on_token_delta_task: 1519
on_fallback_reasons:
  - missing_team_task_argument
  - team_task_token_not_reduced
  - token_not_reduced
  - unsupported_team_task_type
```

case 结果：

```text
switch_off_long: rewrite=false, native_marker_count=18
long_stream: rewrite=true, native_marker_count=0
short_stream: rewrite=false, native_marker_count=1
message_task: rewrite=false, native_marker_count=18
list_task: rewrite=false, native_marker_count=18
none_task: rewrite=false, native_marker_count=0
run_long_indirect: rewrite=true, native_marker_count=0
```

回归验证：

```text
Team rewrite matrix: passed=true
Team rewrite smoke: passed=true
Broadcast real-rewrite smoke: passed=true
Integrated AutoGen-only rewrite smoke: passed=true
ToolSummary rewrite switch: passed=true
Fallback matrix: passed=true
Handoff matrix: passed=true
ToolSummary matrix: passed=true
Native AutoGen smoke: passed=true
unittest: Ran 114 tests OK
compileall: passed
git diff --check: only LF/CRLF warnings
```

可以表述为：

```text
AgentLite 的 AutoGen Team 入口接管已经具备边界守卫：
只有长字符串 task 在开关开启且成本门控通过时才真实替换；
短任务、复杂 task 类型、缺 task、开关关闭等场景都会保留 AutoGen 原生行为并记录 fallback 审计。
```

不能表述为：

```text
已经支持所有 AutoGen task 类型的 Team 层替换；
已经接管 AutoGen Studio / 网页端；
已经重写 AutoGen 所有内部私有消息队列；
已经覆盖所有 AutoGen Team 实现。
```

下一步建议：

```text
进入真实 AutoGen Team benchmark。
用同一个多 Agent 任务分别运行原生 AutoGen 与 AgentLite 托管 AutoGen；
同时记录 Team 入口改写、agent 输入层改写、StatePool、Prompt View、fallback_count、schema_valid_rate、端到端 token 和输出质量；
证明系统不仅在 smoke 中可改写，也能在接近真实应用的 AutoGen 协作里稳定降低协同开销。
```

## 34. v5.12x AutoGen Team Benchmark 校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12x
性质：真实 AutoGen Team 原生/托管对照 benchmark
```

已实现：

```text
AutoGen Driver phase 更新为 v5.12x；
新增 autogen_team_benchmark_app.py；
新增 run_autogen_team_benchmark.py；
同一个 AutoGen-only 用户程序分别执行 native 与 managed 两组；
native 组直接运行 AutoGen，不注入 AgentLite；
managed 组通过 ManagedProcessLauncher 注入 AutoGen Driver，并启用 real-rewrite + Team rewrite；
报告同时记录首条 Team task 可见 token、Team rewrite trace、StatePool/Prompt View 替换标记、安全回退次数和确定性质量分；
质量判断不依赖 LLM 裁判，避免把网络/API 随机性混入接入验证。
```

与创新方案的对应关系：

| v5.12x 能力 | 对应方案模块 |
|---|---|
| 原生/托管同程序对照 | 跨框架运行时层 / SDK 式透明接管 |
| Team 入口真实替换 | SHP-Control + SHP-State |
| StatePool refs + Prompt View | 三层状态池 / 非文本状态传递 |
| fallback_count 审计 | Contract Guard / 鲁棒性守卫 |
| 质量分不下降 | 成功率之外的交付质量守卫 |
| token 差异记录 | 端到端协作开销评测 |

一轮 benchmark：
```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_team_benchmark.py --output-dir .\runs\v5.12x-autogen-team-benchmark-20260701-001

passed: true
checks: 20 / 20 passed
native_first_stream_tokens: 2071
managed_first_stream_tokens: 1045
visible_input_token_delta_native_minus_managed: 1026
visible_input_token_reduction_ratio: 0.495413
team_native_task_tokens: 2071
team_rewritten_task_tokens: 1045
team_token_delta_native_task_minus_rewrite: 1026
team_native_full_broadcast_tokens: 6213
team_wire_plus_prompt_view_tokens: 900
team_token_delta_native_broadcast_minus_rewrite: 5313
team_fallback_count: 0
agent_applied_count: 3
agent_fallback_count: 0
native_quality_score: 12 / 12
managed_quality_score: 12 / 12
quality_delta_managed_minus_native: 0
```

回归验证：
```text
Team benchmark: passed=true
Team rewrite matrix: passed=true
Team rewrite smoke: passed=true
Integrated AutoGen-only rewrite smoke: passed=true
Fallback matrix: passed=true
Handoff matrix: passed=true
ToolSummary matrix: passed=true
ToolSummary rewrite switch: passed=true
Native AutoGen smoke: passed=true
unittest: Ran 114 tests OK
compileall: passed
git diff --check: only LF/CRLF warnings
```

可以表述为：

```text
AgentLite 已经能够在不修改 AutoGen 用户程序的情况下，通过托管启动方式接管 AutoGen Team 入口任务传递。
原生运行时，Team 接收完整长 task；
托管运行时，Team 接收紧凑的 AGENTLITE_TEAM_REAL_REWRITE v1 内容，并通过 StatePool refs 与 Prompt View 保留后续使用路径。
```

不能表述为：

```text
已经接入 AutoGen Studio / 网页端；
已经覆盖所有 AutoGen Team 实现；
已经支持所有 task 类型的真实改写；
已经在真实 LLM 高并发任务中完成最终性能结论。
```

## 35. v5.12y Release CLI Hardening 校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12y
性质：发行版命令行入口、自检和发布门禁加固
```

已实现：

```text
包版本更新为 0.5.12.post2；
agentlite run/start 新增 --rewrite 预设；
新增 agentlite doctor；
新增 agentlite version；
新增 run_release_gate.py；
release gate 覆盖 CLI help/version/doctor、真实 AutoGen Team benchmark、agentlite run CLI 改写路径、unittest、compileall、git diff --check；
README 增加正式用户命令说明。
```

与创新方案的对应关系：

| v5.12y 能力 | 对应方案模块 |
|---|---|
| `agentlite run --rewrite all` | 跨框架透明运行时接管的发行入口 |
| `doctor` 自检 | 可复现运行环境与工程可交付性 |
| `version` | 包版本与发行追踪 |
| release gate | 比赛/发行前统一门禁 |
| CLI 预设收敛环境变量 | 降低部署和演示复杂度 |

一轮 release gate：
```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_release_gate.py --output-dir .\runs\v5.12y-release-gate-20260701-001

passed: true
cli_help: passed
cli_version: passed
doctor_autogen: passed
autogen_team_benchmark: passed
agentlite_cli_rewrite: passed
unittest: passed
compileall: passed
git_diff_check: passed

doctor:
  version: 0.5.12.post2
  python_version: 3.11.15
  tiktoken: 0.13.0
  autogen_agentchat: 0.7.5
  autogen_core: 0.7.5

agentlite_cli_rewrite:
  agentlite_active: true
  contains_team_rewrite_marker: true
  contains_state_pool_marker: true
  contains_broadcast_manifest: true
  contains_receiver_prompt_views: true
  native_marker_count: 0

benchmark:
  native_first_stream_tokens: 2071
  managed_first_stream_tokens: 1038
  visible_input_token_delta_native_minus_managed: 1033
  team_native_full_broadcast_tokens: 6213
  team_wire_plus_prompt_view_tokens: 894
  team_fallback_count: 0
  agent_fallback_count: 0
  native_quality_score: 12 / 12
  managed_quality_score: 12 / 12
```

可以表述为：

```text
AgentLite 已经从“内部脚本可验证”推进到“命令行可用的 AutoGen Python 代码端托管接管版本”；
用户不需要手动记忆底层 rewrite 环境变量，可以通过 --rewrite 预设启用当前支持的真实改写门控。
```

不能表述为：

```text
已经完成 PyPI 正式发行；
已经接入 AutoGen Studio / 网页端；
已经覆盖所有 AutoGen Team 实现和所有 task 类型；
已经完成多操作系统长期兼容性矩阵。
```

## 36. v5.12z Package Release Gate 校准

版本归属：
```text
主版本：v5 跨框架适配与比赛验证版
小版本：v5.12z
性质：本地 wheel 构建、安装包验证、源码目录外命令行接管校准
```

已实现：

```text
包版本更新为 0.5.12.post3；
新增 run_package_release_gate.py；
验证 wheel 必需文件、元数据、entry point；
验证 pip install --target 后从 target_site import；
验证安装包环境中的 agentlite doctor；
验证安装包环境中的 agentlite run --framework autogen --rewrite all；
修复 Windows 中文长路径下 StatePool audit 文件写入失败；
修复 package gate 中文路径 stdout 编码导致的 import path 误判。
```

与创新方案的对应关系：

| v5.12z 能力 | 对应方案模块 |
|---|---|
| wheel 构建与安装验证 | 工具型交付物、可复现部署 |
| `agentlite` entry point | 跨框架透明运行时接管入口 |
| 安装包环境 AutoGen Team 真实替换 | SHP-State + Prompt View 真实介入底层协作 |
| 长路径安全 StatePool I/O | 工程鲁棒性与 Windows 比赛环境适配 |
| package gate | 发布前可复现门禁 |

一轮 package release gate：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_package_release_gate.py --output-dir .\runs\v5.12z-package-release-gate-<stamp>

passed: true
wheel: <output-dir>\wheelhouse\multi_agent_collaboration_runtime-0.5.12.post3-py3-none-any.whl

build_meta_wheel: passed
inspect_wheel: passed
verify_installed_wheel: passed
installed_version: passed
installed_import_path: passed
installed_doctor_autogen: passed
installed_agentlite_cli_rewrite: passed

installed version: 0.5.12.post3
installed import path under target_site: true

AutoGen Team rewrite:
  rewrite_applied: true
  fallback_reasons: []
  error_count: 0
  native_task_tokens: 2071
  rewritten_task_tokens: 1032
  native_full_broadcast_tokens: 6213
  wire_plus_prompt_view_tokens: 888
  native_marker_count: 0
  quality_score: 12 / 12
```

完整 release gate：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_release_gate.py --output-dir .\runs\v5.12z-release-gate-<stamp>

passed: true
cli_help: passed
cli_version: passed
doctor_autogen: passed
autogen_team_benchmark: passed
agentlite_cli_rewrite: passed
unittest: passed
compileall: passed
git_diff_check: passed
```

可以表述为：

```text
AgentLite 已经具备本地 wheel 构建、安装、命令行启动、AutoGen Python 代码端透明接管、Team 输入真实替换的发行包级证据。
```

不能表述为：

```text
已经发布到 PyPI；
已经完成 GitHub Release；
已经接入 AutoGen Studio / 网页端；
已经覆盖所有 AutoGen Team 实现、所有 task 类型和所有操作系统；
已经完成真实 LLM 高并发长期压测。
```

最终发行产物门禁补充：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\build_release_artifacts.py

passed: true

wheel:
  dist/multi_agent_collaboration_runtime-0.5.12.post3-py3-none-any.whl

sdist:
  dist/multi_agent_collaboration_runtime-0.5.12.post3.tar.gz

inspect_wheel: passed
inspect_sdist: passed
```

精确 size 与 SHA256 由每轮本地构建生成的 `release_artifacts_report.json` 记录；tracked 文档不写入 sdist 自身 hash，避免自引用失效。

收紧 package gate 后的重跑：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_package_release_gate.py --output-dir .\runs\v5.12z-package-release-gate-<stamp>

passed: true
rewrite_applied: true
fallback_reasons: []
error_count: 0
native_full_broadcast_tokens: 6213
wire_plus_prompt_view_tokens: 882
```

## 37. v5.13a AutoGen Core Runtime 通信覆盖

### 37.1 本轮解决的问题

v5.12z 已经证明：

```text
agentlite run --framework autogen -- python app.py
```

可以在 Python 代码端启动 AutoGen 应用，并对 AgentChat / Team 层的消息入口做透明接管；其中 Team 输入已经支持真实替换。

但这仍然不是“所有 AutoGen 通信过程”的完整接管，因为 AutoGen 还有更底层的 Core Runtime 通信入口：

```text
autogen_core.SingleThreadedAgentRuntime.send_message()
autogen_core.SingleThreadedAgentRuntime.publish_message()
```

如果只覆盖 AgentChat / Team，那么直接使用 AutoGen Core 的应用、工具调用链路、发布订阅链路仍可能绕过 AgentLite。

v5.13a 的目标是先把这一层纳入观测与低开销协议化路径。

### 37.2 本轮实现内容

新增 Core Runtime transport shadow 覆盖：

```text
send_message    -> StatePool 写入 -> SHP shadow envelope -> receiver Prompt View -> trace 指标
publish_message -> StatePool 写入 -> SHP shadow envelope -> receiver Prompt View -> trace 指标
```

新增或修改的关键文件：

```text
agent_runtime/drivers/autogen.py
agent_runtime/eval/trace_logger.py
examples/autogen_core_transport_smoke.py
examples/run_autogen_core_transport_smoke.py
docs/experiments/v5.13a-autogen-core-transport-results.md
```

新增 trace 事件：

```text
autogen_transport_input_state
autogen_core_transport_shadow
```

其中 `autogen_core_transport_shadow` 记录：

```text
method
sender
declared_receiver
state_refs
shadow_wire_envelope
schema_valid
prompt_view_available
native_transport_tokens
shp_shadow_envelope_tokens
prompt_view_tokens
wire_plus_prompt_view_tokens
token_delta_native_minus_wire_plus_prompt_view
communication_gate
```

### 37.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_transport_smoke.py --output-dir .\runs\v5.13a-autogen-core-transport-20260702-003
```

结果：

```text
passed: true
driver_phase_v5_13a: true
direct_and_publish_received: true
transport_state_event_count: 2
core_transport_shadow_event_count: 2
core_transport_methods: publish_message, send_message
core_transport_receivers: agent_direct_agent_default, topic_core-topic_default
core_transport_state_ref_count: 2
core_transport_schema_valid_count: 2
core_transport_prompt_view_count: 2
core_transport_token_delta: 2222
```

回归验证：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe -m unittest discover -s tests

Ran 117 tests in 23.032s
OK
```

### 37.4 当前边界

可以表述为：

```text
AgentLite 的 AutoGen 接管范围已经从 AgentChat / Team 层下探到 AutoGen Core Runtime 的 send_message / publish_message 入口；
Core Runtime 直接消息和发布订阅消息已经可以写入状态池、生成紧凑 SHP 影子包、渲染接收方 Prompt View，并产生成本与结构化 trace 指标。
```

仍不能表述为：

```text
已经真实替换所有 AutoGen Core 用户自定义消息对象；
已经接管 AutoGen Studio / 网页端进程；
已经覆盖分布式运行时、远程 runtime、所有第三方封装和所有自定义序列化协议。
```

原因是 AutoGen Core 的 handler 通常按 Python 消息类型分发。任意把用户自定义 dataclass / pydantic 消息替换成通用 SHP 对象，可能导致 handler 类型匹配失败。

因此，下一步更稳的方向不是“粗暴替换所有对象”，而是做类型保持的真实改写：

```text
当 Core 消息对象含有 content / body / text 等可替换字段时，
保持原 Python 类型不变，只把长文本字段替换为 SHP state_ref wire packet，
从而既不破坏 AutoGen Core 的类型分发，也能真正减少底层传输文本。
```

## 38. v5.13b AutoGen Core 类型保持真实改写

### 38.1 本轮解决的问题

v5.13a 解决的是 Core Runtime 通信入口能被 AgentLite 看到并协议化：

```text
send_message / publish_message -> StatePool -> SHP shadow -> Prompt View -> trace
```

但 v5.13a 仍然没有改变真实传输给 AutoGen Core handler 的消息对象。也就是说，它能证明“看见并生成低开销替代包”，但不能证明“真实替换底层传输内容”。

v5.13b 补的是这一层：

```text
当 Core message 含有 content / body / text 字符串字段时，
保持原 Python message 类型不变，
只把该长文本字段替换为 AgentLite SHP state_ref wire packet + Prompt View。
```

这避免了粗暴替换整个对象导致 AutoGen Core handler 类型分发失败的问题。

### 38.2 本轮实现内容

新增环境开关：

```text
AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE=1
```

`agentlite run --framework autogen --rewrite all -- python app.py` 的现有命令形式不变；`--rewrite all` 现在会开启：

```text
AGENTLITE_AUTOGEN_BROADCAST_MODE=real-rewrite
AGENTLITE_AUTOGEN_TEAM_REWRITE=1
AGENTLITE_AUTOGEN_HANDOFF_REWRITE=1
AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE=1
AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE=1
```

新增 trace 事件：

```text
autogen_core_content_real_rewrite
```

它记录：

```text
method
transport_metadata
native_message_type
rewritten_field
state_refs
schema_valid
prompt_view_available
native_content_tokens
rewritten_content_tokens
token_delta_native_minus_rewrite
semantic_checks
fallback_reasons
```

新增或修改的关键文件：

```text
agent_runtime/drivers/autogen.py
agent_runtime/cli.py
examples/autogen_core_transport_smoke.py
examples/run_autogen_core_content_rewrite_smoke.py
docs/experiments/v5.13b-autogen-core-transport-results.md
docs/experiments/v5.13b-autogen-core-content-rewrite-results.md
```

### 38.3 验证结果

真实改写 smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_content_rewrite_smoke.py --output-dir .\runs\v5.13b-autogen-core-rewrite-20260702-001

passed: true
driver_phase_v5_13b: true
core_content_rewrite_enabled: true
native_type_preserved_at_receiver: true
payload_kind_preserved_at_receiver: true
rewrite_marker_received: true
content_shortened_at_receiver: true
send_and_publish_rewritten: true
rewrite_applied_without_fallback: true
schema_valid: true
prompt_view_available: true
token_reduced: true
```

核心指标：

```text
autogen_core_content_real_rewrite: 2
rewrite_applied_count: 2
rewrite_fallback_count: 0
methods: publish_message, send_message
state_ref_count: 2
token_delta_native_minus_rewrite: 2158
```

接收方证据：

```text
received message type: CorePayload
payload_kind: core_transport_state
direct content chars: 3420 -> 1291
publish content chars: 3926 -> 1289
```

默认 shadow 回归：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_transport_smoke.py --output-dir .\runs\v5.13b-autogen-core-transport-20260702-001

passed: true
core_transport_shadow_event_count: 2
agentlite_rewrite_marker: false
```

这说明真实改写受开关控制，不会默认改变所有 Core 消息行为。

### 38.4 当前边界

可以表述为：

```text
AgentLite 已经能在 AutoGen Core Runtime 的 send_message / publish_message 入口，
对含有 content / body / text 文本字段的 Core 消息做类型保持真实改写。
```

仍不能表述为：

```text
已经无条件接管所有任意 Python 对象；
已经接管 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport 和所有第三方 AutoGen 封装。
```

下一步应继续补：

```text
1. receiver 侧 Prompt View 透明解析 / 还原策略；
2. AutoGen Studio / 网页端后端进程的启动注入验证；
3. 更多 Core message 结构的安全改写矩阵，例如 dataclass、pydantic、dict、namedtuple、普通可变对象。
```

## 39. v5.13c AutoGen Core 接收端 Prompt View Hydration

### 39.1 本轮解决的问题

v5.13b 已经可以在 AutoGen Core Runtime 的 `send_message` / `publish_message` 入口做类型保持真实改写：

```text
CorePayload(content=长文本)
-> CorePayload(content=AGENTLITE_CORE_CONTENT_REWRITE + shp_wire + prompt_view)
```

但这还不够“无痛”，因为用户 handler 会直接看到 AgentLite wire marker，需要自己理解 `shp_wire`。

v5.13c 补的是接收端透明转换：

```text
BaseAgent.on_message(message, ctx)
-> 检测 message.content 是否为 AgentLite Core rewrite wire
-> 从 StatePool 渲染 Prompt View
-> 保持原 Python message 类型不变
-> 只把 content 替换为 Prompt View
-> 再进入用户 handler
```

这样 handler 不需要自己解析 `shp_wire`。

### 39.2 本轮实现内容

新增 patch target：

```text
core_agent: BaseAgent.on_message
```

新增环境变量：

```text
AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE=prompt-view
```

`agentlite run --framework autogen --rewrite all -- python app.py` 的命令形式不变；`--rewrite all` 现在额外打开：

```text
AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE=prompt-view
```

新增 trace 事件：

```text
autogen_core_receiver_hydration
```

它记录：

```text
native_message_type
hydrated_field
state_refs
prompt_view_available
wire_marker_removed
original_rewritten_tokens
hydrated_content_tokens
semantic_checks
fallback_reasons
```

新增或修改的关键文件：

```text
agent_runtime/drivers/autogen.py
agent_runtime/cli.py
examples/autogen_core_transport_smoke.py
examples/run_autogen_core_content_rewrite_smoke.py
tests/test_autogen_core_rewrite.py
docs/experiments/v5.13c-autogen-core-transport-results.md
docs/experiments/v5.13c-autogen-core-content-rewrite-results.md
```

### 39.3 验证结果

真实改写 + 接收端 hydration smoke：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_content_rewrite_smoke.py --output-dir .\runs\v5.13c-autogen-core-hydration-20260702-001

passed: true
driver_phase_v5_13c: true
core_content_rewrite_enabled: true
core_receiver_hydrate_prompt_view: true
native_type_preserved_at_receiver: true
payload_kind_preserved_at_receiver: true
rewrite_marker_not_leaked_to_receiver: true
prompt_view_received: true
content_shortened_at_receiver: true
send_and_publish_rewritten: true
rewrite_applied_without_fallback: true
receiver_hydration_applied_without_fallback: true
```

核心指标：

```text
autogen_core_content_real_rewrite: 2
autogen_core_receiver_hydration: 2
rewrite_applied_count: 2
rewrite_fallback_count: 0
hydration_applied_count: 2
hydration_fallback_count: 0
token_delta_native_minus_rewrite: 2166
```

接收方证据：

```text
received message type: CorePayload
payload_kind: core_transport_state
direct content chars: 3420 -> 352
publish content chars: 3926 -> 352
agentlite_rewrite_marker: false
agentlite_prompt_view: true
```

默认 shadow 回归：

```text
命令：F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_transport_smoke.py --output-dir .\runs\v5.13c-autogen-core-transport-20260702-001

passed: true
core_receiver_hydrate_mode: off
agentlite_rewrite_marker: false
agentlite_prompt_view: false
```

### 39.4 当前边界

可以表述为：

```text
AgentLite 已经覆盖 AutoGen Core 的发送端 send_message / publish_message 和接收端 BaseAgent.on_message；
对于含有 content / body / text 文本字段的消息，已经可以做到类型保持真实改写，并在接收端自动转换为 Prompt View。
```

仍不能表述为：

```text
已经自动接管 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经对所有任意 Python 对象保证语义等价；
已经证明真实 LLM agent 在 Prompt View 下质量不下降。
```

下一步应继续补：

```text
1. Core message 结构矩阵：dataclass / dict / pydantic / namedtuple / mutable object；
2. AutoGen Studio 或网页端后端进程注入验证；
3. AssistantAgent / GroupChat / Core 混合端到端链路。
```

## 40. v5.13d AutoGen Core 消息结构矩阵

### 40.1 本轮解决的问题

v5.13c 已经证明单一 `CorePayload.content` 可以完成：

```text
send_message / publish_message 发送端真实改写；
BaseAgent.on_message 接收端 Prompt View hydration；
原 Python 类型保持不变；
用户 handler 不直接看到 AgentLite wire。
```

但真实框架和网页端后端不一定都使用名为 `content` 的 dataclass 消息。常见情况还包括：

```text
body 字段；
text 字段；
Pydantic BaseModel；
publish_message 广播路径；
其他适配器传入 dict / namedtuple / 普通对象。
```

v5.13d 的目标是证明当前 Core 接管机制不是只对一个 demo 类有效。

### 40.2 本轮实现内容

新增真实 AutoGen Core matrix smoke：

```text
examples/autogen_core_message_matrix_smoke.py
examples/run_autogen_core_message_matrix_smoke.py
```

真实 AutoGen smoke 覆盖：

```text
dataclass + content
dataclass + body
dataclass + text
pydantic BaseModel + content
publish_message + content
```

helper 单测扩展覆盖：

```text
dict + body
namedtuple + body
pydantic BaseModel + content
dataclass + content
普通可变对象 + text
```

说明：

```text
真实 AutoGen Core handler 不支持裸 dict 作为 handler 消息类型；
本地验证时 AutoGen 会报 ValueError: No serializers found for type <class 'dict'>。
因此 dict 不放进真实 AutoGen smoke，而放进底层 helper 单测。
```

### 40.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_message_matrix_smoke.py --output-dir .\runs\v5.13d-autogen-core-matrix-20260702-001
```

结果：

```text
passed: true
driver_phase_v5_13d: true
all_cases_received: true
direct_and_publish_received: true
expected_fields_received: true
expected_types_received: true
rewrite_events_for_all_messages: true
hydration_events_for_all_messages: true
rewrite_applied_without_fallback: true
hydration_applied_without_fallback: true
rewrite_types_cover_matrix: true
hydration_types_cover_matrix: true
rewrite_fields_cover_matrix: true
hydration_fields_cover_matrix: true
wire_marker_not_leaked: true
prompt_view_received: true
content_shortened_at_receiver: true
token_reduced: true
```

矩阵覆盖：

```text
received_cases:
  dataclass_body
  dataclass_content
  dataclass_content_publish
  dataclass_text
  pydantic_content

received_types:
  MatrixBodyPayload
  MatrixContentPayload
  MatrixPydanticPayload
  MatrixTextPayload

received_fields:
  body
  content
  text
```

核心指标：

```text
autogen_core_content_real_rewrite: 5
autogen_core_receiver_hydration: 5
rewrite_applied_count: 5
rewrite_fallback_count: 0
hydration_applied_count: 5
hydration_fallback_count: 0
autogen_core_transport_shadow: 5
token_delta_native_minus_rewrite: 2870
```

### 40.4 当前边界

可以表述为：

```text
AgentLite 的 AutoGen Core 接管已经不局限于单一 content dataclass；
对 AutoGen Core 支持的 dataclass / Pydantic 消息，以及 content / body / text 字段，
已经能完成发送端真实改写和接收端 Prompt View hydration。
```

仍不能表述为：

```text
已经自动接管 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经对所有任意 Python 对象保证真实 AutoGen handler 支持；
已经证明真实 LLM agent 在 Prompt View 输入下质量不下降。
```

下一步应继续补：

```text
1. AutoGen Studio / 网页端后端进程注入验证；
2. AssistantAgent / GroupChat / Core 混合端到端链路；
3. 真实 LLM agent 的质量和成本对比。
```

## 46. v5.13i AutoGen Studio Provider Token 采集

### 46.1 背景

用户侧网页端实验应使用 AutoGen Studio 原生网页，而不是项目自建网页。AutoGen Studio 的浏览器页面可以展示运行过程，但浏览器本身不能可靠导出结构化 token 数据，也无法区分模型服务实际计费 token 与 Agent 间协作通信 token。

因此 v5.13i 的目标是补上 provider token 采集：

```text
provider token:
  模型服务返回的 prompt/completion usage，更接近后台账单口径。

collaboration token:
  Agent 间消息传递、StateRef、Prompt View 等协作通信成本。
```

### 46.2 已完成实现

代码变更：

```text
agent_runtime/drivers/autogen.py:
  DRIVER_PHASE = v5.13i
  SUPPORTED_MODULE_ROOTS 增加 autogen_ext
  PATCH_TARGETS 增加 model_client: create / create_stream
  新增 autogen_model_client_usage trace 事件

web_monitor/parser.py:
  汇总 autogen_model_client_usage 到 llm_prompt_tokens /
  llm_completion_tokens / llm_total_tokens / llm_call_count

agent_runtime/eval/autogen_session_report.py:
  report 中显示 llm_call_count 和 llm_* token

docs/experiments/autogen-native-code-and-studio-user-experiment.md:
  将网页端实验口径修正为 AutoGen Studio 原生网页
```

### 46.3 验收结果

本轮没有安装并启动真实 AutoGen Studio；使用 fake `autogen_ext.models.openai.OpenAIChatCompletionClient` 验证注入链路。

通过测试：

```text
python -m compileall agent_runtime web_monitor examples/developer_autogen_code_app.py
python -m unittest tests.test_launcher tests.test_autogen_session_report tests.test_web_monitor

Ran 21 tests in 0.796s
OK
```

### 46.4 当前边界

可以表述为：

```text
AgentLite 在被其启动的 AutoGen / AutoGen Studio 后端 Python 进程内，
能够旁路 hook AutoGen 模型客户端 create/create_stream，
记录 provider usage，并在 session report 中汇总展示。
```

仍不能表述为：

```text
已经在真实 AutoGen Studio 环境完成端到端人工网页实验；
已经提供 agentlite autogen-studio 便捷命令；
已经对不返回 usage 的第三方模型客户端保证自动统计真实后台账单。
```

## 44. v5.13h 安装包级 AutoGen 混合接管门禁

### 44.1 本轮解决的问题

v5.13g 已经证明源码目录内可以完成：

```text
RoundRobinGroupChat.run_stream(task=...)
-> BaseChatAgent.on_messages()
-> SingleThreadedAgentRuntime.send_message(sender=...)
-> Core worker
-> Team final output
```

但这仍不能证明用户安装 AgentLite 后，在普通项目目录里用 CLI 启动 AutoGen 脚本也能生效。v5.13h 的目标是补上“安装包级别”的工程门禁。

### 44.2 本轮实现内容

新增 package gate 检查项：

```text
1. 构建 multi_agent_collaboration_runtime-0.5.13.dev0 wheel；
2. 检查 wheel metadata、entry point、必需运行时模块；
3. 确认 wheel 内没有 runs/dist/build/.git 等本地产物；
4. 安装到隔离 target_site；
5. 从源码目录外执行 python -m agent_runtime.cli version / doctor；
6. 从安装包环境执行 Team rewrite smoke；
7. 增加 `agentlite autogen -- ...` 专用入口；
8. 从安装包环境通过 `agentlite autogen -- ...` 执行 mixed Team/Core smoke。
```

核心修改：

```text
pyproject.toml: version = 0.5.13.dev0
agent_runtime/__init__.py: __version__ = 0.5.13.dev0
agent_runtime/drivers/autogen.py: DRIVER_PHASE = v5.13h
agent_runtime/cli.py: 增加 agentlite autogen 专用子命令，默认 rewrite=all
examples/run_package_release_gate.py: 增加 installed_agentlite_cli_mixed_team_core 检查，并使用 agentlite autogen 入口
```

新增实验文档：

```text
docs/experiments/v5.13h-package-gate-results.md
```

### 44.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_package_release_gate.py --output-dir .\runs\v5.13h-package-gate-20260702-002
```

总结果：

```text
passed: true
wheel: runs\v5.13h-package-gate-20260702-002\wheelhouse\multi_agent_collaboration_runtime-0.5.13.dev0-py3-none-any.whl
```

wheel 检查：

```text
inspect_wheel: passed
metadata name: true
metadata version_dev0: true
requires_tiktoken: true
requires_python >=3.11: true
entry_point agentlite: true
missing_members: []
forbidden_members: []
```

安装包检查：

```text
verify_installed_wheel: passed
installed version: 0.5.13.dev0
installed import path under target_site: true
doctor autogen: true
```

Team 改写证据：

```text
installed_agentlite_cli_rewrite: passed
agentlite_active: true
contains_team_rewrite_marker: true
contains_state_pool_marker: true
contains_broadcast_manifest: true
contains_receiver_prompt_views: true
native_marker_count: 0
```

Team/Core 混合链路证据：

```text
installed_agentlite_cli_mixed_team_core: passed
command entry: python -m agent_runtime.cli autogen -- python examples\autogen_mixed_team_core_smoke.py
agentlite_active: true

bridge_seen_first_message:
  contains_team_rewrite_marker: true
  contains_state_pool_marker: true
  contains_broadcast_manifest: true
  contains_receiver_prompt_views: true
  contains_team_native_marker: false

core_received_first:
  message_type: MixedCoreRequest
  agentlite_prompt_view: true
  contains_core_request_native_marker: false
  contains_core_rewrite_marker: false

core_caller_reply_first:
  message_type: MixedCoreReply
  agentlite_prompt_view: true
  contains_core_reply_native_marker: false
  contains_core_rewrite_marker: false

final_message:
  contains_done_token: true
  contains_team_rewrite_marker: false
  contains_state_pool_marker: false
  contains_broadcast_manifest: false
```

### 44.4 当前边界

可以表述为：

```text
AgentLite 已经具备安装包级别的 AutoGen 单进程接管能力。用户通过 CLI 启动普通 AutoGen Python 脚本时，Team 入口消息、AgentChat Agent 输入、Core request、Core response 可以被 AgentLite 改写为状态引用和 Prompt View，最终输出不会泄漏 AgentLite wire marker。
```

仍不能表述为：

```text
已经自动注入 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经完成真实 LLM agent 的质量不下降实验。
```

下一步应继续补：

```text
1. AutoGen Studio / 网页端后端进程注入验证；
2. 分布式 / 远程 runtime transport 的边界调研；
3. 真实 LLM agent 在 Prompt View 输入下的质量、成本、延迟对比。
```

## 45. v5.13h AutoGen Web 后端进程接管补充验证

### 45.1 本轮解决的问题

`agentlite autogen -- python app.py` 已经证明可以托管普通 AutoGen 脚本，但用户的目标还包括“网页端使用 AutoGen 框架时，底层协作也能被接管”。在真正接入 AutoGen Studio 前，需要先验证一个更基础的问题：

```text
如果 AutoGen 不是在脚本入口立刻运行，而是在 Web 后端收到 HTTP 请求后才创建和运行，AgentLite 的进程级注入是否仍然有效？
```

### 45.2 本轮实现内容

新增 Web 后端 smoke：

```text
examples/autogen_web_backend_smoke.py
examples/run_autogen_web_backend_smoke.py
docs/experiments/v5.13h-autogen-web-backend-results.md
```

验证形态：

```text
1. 使用 agentlite autogen 启动一个 Python HTTP 后端；
2. 后端启动后监听本地端口；
3. runner 向 /run 发送 HTTP 请求；
4. HTTP handler 内部创建 AutoGen RoundRobinGroupChat；
5. Team task 仍被改写为 StatePool 引用和 Prompt View；
6. 最终 HTTP 响应不泄漏 AgentLite wire marker。
```

### 45.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_web_backend_smoke.py --output-dir .\runs\v5.13h-autogen-web-backend-20260702-001
```

结果：

```text
passed: true
returncode: 0
driver_phase: v5.13h
```

检查项：

```text
agentlite_autogen_command_returncode_zero: true
bootstrap_status_present: true
bootstrap_ok: true
hooks_active: true
driver_phase_v5_13h: true
app_output_written: true
http_status_ok: true
agentlite_active_in_web_backend: true
web_request_path_recorded: true
team_rewrite_env_enabled: true
first_http_task_rewritten: true
native_http_task_removed: true
team_rewrite_event_recorded: true
team_rewrite_applied: true
team_rewrite_no_fallback: true
final_output_contains_done: true
final_output_not_agentlite_wire: true
```

核心指标：

```text
native_task_tokens: 1869
rewritten_task_tokens: 1048
token_delta_native_task_minus_rewrite: 821
native_full_broadcast_tokens: 5607
wire_plus_prompt_view_tokens: 903
token_delta_native_broadcast_minus_rewrite: 4704
fallback_count: 0
```

### 45.4 当前边界

可以表述为：

```text
AgentLite 能接管由 agentlite autogen 启动的 Python Web 后端进程；即使 AutoGen Team 是在后续 HTTP 请求 handler 内创建的，也仍然会被改写。
```

仍不能表述为：

```text
已经能附着到未通过 agentlite 启动的、已经运行中的 Web 后端进程；
已经自动识别并接管 AutoGen Studio 的具体启动命令；
已经覆盖多 worker、多进程、远程 runtime 或分布式 transport。
```

## 42. v5.13f AutoGen Core 最终输出通道保护

### 42.1 本轮解决的问题

v5.13e 补上了 Core `send_message()` 的返回路径，但它也暴露出一个边界风险：

```text
如果所有 send_message 返回值都被压缩成 Prompt View，
那么最终用户交付物也可能被压缩，
这会和“最终交付内容必须完整输出”的系统要求冲突。
```

v5.13f 的核心修正是区分两类路径：

```text
有 sender 的 send_message：视为 Agent-to-Agent 内部通信，可以低开销化；
无 sender 的 send_message：视为外部调用或最终输出边界，返回值保持原始完整内容。
```

### 42.2 本轮实现内容

driver 修改：

```text
1. Runtime.send_message 进入时检查 sender；
2. 只有 sender 存在时，才开启 Core response rewrite 计数；
3. BaseAgent.on_message 返回值只在内部通信链路中被改写；
4. 无 sender 的外部调用仍可压缩请求输入，但返回值不压缩；
5. 最终输出完整内容继续由用户代码直接获得。
```

新增验证：

```text
examples/autogen_core_final_output_guard_smoke.py
examples/run_autogen_core_final_output_guard_smoke.py
docs/experiments/v5.13f-autogen-core-final-output-guard-results.md
```

同时更新内部 response smoke，使它注册 `caller` Agent 并通过 `sender=AgentId("caller", "default")` 明确模拟 Agent-to-Agent 通信。

### 42.3 验证结果

内部通信返回路径：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_response_rewrite_smoke.py --output-dir .\runs\v5.13f-autogen-core-response-20260702-002

passed: true
driver_phase_v5_13f: true
request_rewrite_event_count: 4
request_hydration_event_count: 4
response_rewrite_event_count: 4
response_hydration_event_count: 4
request_token_delta_native_minus_rewrite: 1977
response_token_delta_native_minus_rewrite: 2035
```

最终输出保护：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_final_output_guard_smoke.py --output-dir .\runs\v5.13f-autogen-core-final-20260702-001

passed: true
driver_phase_v5_13f: true
request_rewrite_event_count: 1
request_hydration_event_count: 1
response_rewrite_event_count: 0
response_hydration_event_count: 0
native_final_chars: 3322
reply_content_chars: 3322
reply_full_native_preserved: true
```

回归验证：

```text
Core transport shadow: passed, token_delta 2188
Core receiver hydration: passed, token_delta 2164
Core message matrix: passed, rewrite 5, hydration 5, token_delta 2884
unittest discover -s tests: 125 tests OK
```

### 42.4 当前边界

可以表述为：

```text
AgentLite 已经能在单进程 AutoGen Core 中区分内部协作消息和最终输出边界；
内部协作消息可以低开销化；
最终输出返回值保持完整原文。
```

仍不能表述为：

```text
已经自动接管 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经完成真实 LLM agent 的质量对比；
已经对所有任意 Python 对象和所有 AutoGen 扩展类型提供无条件语义等价保证。
```

下一步建议：

```text
1. 做 AgentChat / Team / Core 混合链路端到端 smoke；
2. 再进入 AutoGen Studio / 网页端后端进程注入验证；
3. 最后做真实 LLM agent 质量与成本对比。
```

## 43. v5.13g AgentChat / Team / Core 混合链路

### 43.1 本轮解决的问题

v5.13f 已经分别证明：

```text
Team 入口可以真实改写；
Core 请求/返回可以真实改写和 Prompt View hydration；
最终输出边界可以保持完整原文。
```

但这些还属于分开验证。v5.13g 需要证明：当一个真实 AutoGen 用户脚本同时使用 `RoundRobinGroupChat`、`BaseChatAgent` 和 `SingleThreadedAgentRuntime` 时，AgentLite 的多层 hook 不会互相冲突。

### 43.2 本轮实现内容

新增混合链路 smoke：

```text
examples/autogen_mixed_team_core_smoke.py
examples/run_autogen_mixed_team_core_smoke.py
docs/experiments/v5.13g-autogen-mixed-team-core-results.md
```

验证链路：

```text
RoundRobinGroupChat.run_stream(task=长文本)
-> Team 入口 rewrite
-> MixedBridgeAgent.on_messages()
-> Agent 内部调用 SingleThreadedAgentRuntime.send_message(sender=...)
-> Core request rewrite + receiver hydration
-> Core response rewrite + caller hydration
-> MixedBridgeAgent 返回 DONE_MIXED
-> Team final output 不泄漏 AgentLite wire
```

本轮还修正了一个工程问题：

```text
Team 入口 rewrite 生成的 AgentLite 包进入 AgentChat Agent 后，
AgentChat 输入层不应再次把它包成 AGENTLITE_REAL_REWRITE。
v5.13g 增加 already_agentlite_rewritten 保护，遇到已有 AgentLite rewrite marker 的消息时跳过二次改写。
```

### 43.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_mixed_team_core_smoke.py --output-dir .\runs\v5.13g-autogen-mixed-team-core-20260702-002
```

结果：

```text
passed: true
driver_phase_v5_13g: true
team_rewrite_event_recorded: true
team_rewrite_applied: true
team_rewrite_no_fallback: true
team_rewrite_reduces_tokens: true
bridge_agent_saw_team_packet: true
core_worker_saw_prompt_view_request: true
core_caller_saw_prompt_view_reply: true
core_request_rewrite_and_hydration_recorded: true
core_response_rewrite_and_hydration_recorded: true
core_request_no_fallback: true
core_response_no_fallback: true
team_final_output_contains_done: true
team_final_output_not_agentlite_wire: true
```

核心指标：

```text
team native task chars: 9758
bridge seen team packet chars: 1636
team token_delta_native_broadcast_minus_rewrite: 1409

core native request chars: 6678
core worker received request chars: 352
core_request_rewrite_event_count: 1
core_request_hydration_event_count: 1
core_request_token_delta_native_minus_rewrite: 818

core native reply chars: 7442
core caller received reply chars: 350
core_response_rewrite_event_count: 1
core_response_hydration_event_count: 1
core_response_token_delta_native_minus_rewrite: 966
```

回归验证：

```text
Core final output guard: passed
Core response rewrite: passed
Core message matrix: passed
Core receiver hydration: passed
Core transport shadow: passed
Team rewrite smoke: passed
AgentChat integrated rewrite: passed
unittest discover -s tests: 125 tests OK
```

### 43.4 当前边界

可以表述为：

```text
AgentLite 已经能在一个 AutoGen Python 进程中同时接管 Team 入口、AgentChat Agent 输入、Core request/response，并保护最终输出。
```

仍不能表述为：

```text
已经自动注入 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经完成真实 LLM agent 的质量对比。
```

下一步建议：

```text
1. 进入 AutoGen Studio / 网页端后端进程注入验证；
2. 或先做 package gate，确认 v5.13g 能以安装包方式从源码外运行；
3. 再做真实 LLM agent 质量与成本对比。
```

## 41. v5.13e AutoGen Core 请求-响应返回路径接管

### 41.1 本轮解决的问题

v5.13d 证明了 Core 请求消息可以完成：

```text
Runtime.send_message / publish_message 发送端真实改写；
BaseAgent.on_message 接收端 Prompt View hydration；
用户 handler 不直接看到 AgentLite wire。
```

但 `send_message()` 在 AutoGen Core 中常常是 RPC 风格调用：接收方 handler 会返回一个消息对象给调用方。这个返回对象如果很长，本质上也是 Agent 间通信。v5.13e 补上了这条反向通信路径。

### 41.2 本轮实现内容

新增 driver 能力：

```text
1. Runtime.send_message 运行期间记录 Core 请求-响应链路；
2. BaseAgent.on_message 的 handler 返回值如果包含 content/body/text 长文本字段，则写入 StatePool；
3. handler 返回给 Runtime 的对象保持原 Python 类型，但文本字段替换为 SHP state_ref wire；
4. Runtime.send_message 返回给调用方前，再将 wire 水化为 Prompt View；
5. 调用方拿到的仍是原 Python 返回类型，看不到 AgentLite wire marker。
```

新增实现文件：

```text
examples/autogen_core_response_rewrite_smoke.py
examples/run_autogen_core_response_rewrite_smoke.py
docs/experiments/v5.13e-autogen-core-response-rewrite-results.md
```

相关 driver 事件：

```text
autogen_core_response_real_rewrite
autogen_core_response_hydration
```

### 41.3 验证结果

命令：

```text
F:\software\anaconda\envs\agentlite-autogen\python.exe .\examples\run_autogen_core_response_rewrite_smoke.py --output-dir .\runs\v5.13e-autogen-core-response-20260702-002
```

结果：

```text
passed: true
driver_phase_v5_13e: true
all_requests_received: true
all_replies_returned: true
reply_fields_cover_matrix: true
reply_types_cover_matrix: true
request_rewrite_events_for_all_messages: true
request_hydration_events_for_all_messages: true
response_rewrite_events_for_all_replies: true
response_hydration_events_for_all_replies: true
request_wire_marker_not_leaked: true
reply_wire_marker_not_leaked: true
reply_prompt_view_returned: true
reply_content_shortened: true
request_token_reduced: true
response_token_reduced: true
```

核心指标：

```text
autogen_core_content_real_rewrite: 4
autogen_core_receiver_hydration: 4
autogen_core_response_real_rewrite: 4
autogen_core_response_hydration: 4
request_rewrite_fallback_count: 0
request_hydration_fallback_count: 0
response_rewrite_fallback_count: 0
response_hydration_fallback_count: 0
request_token_delta_native_minus_rewrite: 1983
response_token_delta_native_minus_rewrite: 2057
```

调用方返回对象证据：

```text
reply_types:
  ResponseBodyReply
  ResponseContentReply
  ResponsePydanticReply
  ResponseTextReply

reply_fields:
  body
  content
  text

native reply max chars: 4346
caller reply chars after hydration: 348, 342, 342, 350
agentlite_rewrite_marker: false
agentlite_prompt_view: true
```

### 41.4 当前边界

可以表述为：

```text
AgentLite 已经在单进程 AutoGen Core SingleThreadedAgentRuntime 中覆盖 send_message 请求方向、publish_message 广播方向、BaseAgent.on_message 接收方向，以及 send_message 返回方向。
```

仍不能表述为：

```text
已经自动接管 AutoGen Studio / 网页端后端进程；
已经覆盖分布式 runtime、远程 runtime、跨进程 transport；
已经对所有任意 Python 对象保证真实 AutoGen handler 支持；
已经证明真实 LLM agent 在 Prompt View 输入下质量不下降。
```

下一步应继续补：

```text
1. AutoGen Studio / 网页端后端进程注入验证；
2. AssistantAgent / GroupChat / Core 混合端到端链路；
3. 真实 LLM agent 的质量和成本对比。
```
