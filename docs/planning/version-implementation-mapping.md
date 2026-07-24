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

## 54. v5.13q：连续任务必要记忆优先于纯 Token 门禁

### 54.1 根因

真实 Studio A3 已命中 A2 的 739 Token MemoryView，但当前任务只有 37 Token，候选改写被 `team_task_token_not_reduced` 和 `token_not_reduced` 拒绝。记忆存在且检索正常，实际注入为 0，导致 Agent 声称缺少上一轮结果。

### 54.2 实现

1. 以中英文通用连续性表达和具名步骤引用识别依赖既有上下文的任务；
2. 只有存在已准入、同协作组的检索命中时，才允许连续性覆盖纯 Token 门禁；
3. Schema、StateRef、Prompt View、接收者和消息类型门禁仍然强制执行；
4. 新增 `continuity_required_event_count`、`continuity_cost_override_count` 和 `continuity_memory_injection_count`；
5. 覆盖成本门禁后的实际 Token 仍进入端到端成本，允许报告负节省，不把正确性成本伪装为优化收益。

### 54.3 边界

该机制不包含 Question A 或旅游规则，也不允许 pending/rejected 记忆绕过候选池准入。无检索命中、无明确连续性信号或存在结构错误时，原成本与可靠性门禁保持不变。

本地验证已通过 192 项全量测试与完整 release gate；真实 Studio A1-A10 需使用全新数据目录重跑确认。

## 55. v5.13r：最小充分角色视图与端到端核算

### 55.1 对 v5.13q 的校正

v5.13q 只解决必要记忆被纯 Token 门禁丢弃的问题；v5.13r 进一步避免把公共 MemoryView 和完整当前任务重复发送给所有 Agent。

### 55.2 实现

1. 框架无关、本地规则优先的 Planner / Writer / Reviewer / General 角色视图；
2. 当前用户任务和已准入 MemoryView 分别生成最小充分角色视图；
3. AutoGen Team 只携带接收者分区，Agent 调用前只水合自己的分区；
4. 连续任务缺少明确字段时，通过来源 StateRef 解析受限相关片段；
5. 同时记录任务视图、记忆视图、字段补取、端到端协作 Token 和最终交付规则通过率；
6. 不使用 Question A 专用规则，不调用控制 LLM，不绕过 Schema、消息结构、协作组隔离和记忆准入。

### 55.3 验证与边界

本地 198 项测试和完整 release gate 通过。MemoryView 跨启动持久化已保留；来源状态的跨进程字段展开仍需共享 StatePool 索引持久化。

## 56. v5.13s：动态能力画像与能力动作视图

### 56.1 对 v5.13r 的校正

v5.13r 用 Planner、Writer、Reviewer 和 General 验证了最小充分上下文，但生产视图仍依赖固定职责分类。最终创新方案要求角色只作为能力来源之一，工具变化和运行结果也必须更新画像，任意名称的 Agent 都应被支持。

### 56.2 实现

1. Capability Profile Manager 按 role、tool、runtime 三来源维护版本化画像；
2. Tool-Capability Synchronizer 自动处理工具增加、移除、来源与成本；
3. Capability Router 使用最终方案 RouteScore，区分 active 路由和 AutoGen advisory 路由；
4. Cold Start Tie Resolver 按专用度、工具、记忆局部性、负载、Schema、成本和确定性哈希处理同类平局；
5. 只有语义不确定才动态寻找具备任务分解或路由能力的 Agent，不写死 Planner 名称；
6. 当前任务、状态和记忆均按真实接收者能力与当前动作生成最小上下文视图；
7. AutoGen Driver 自动发现真实 Agent 的说明、系统提示、工具、输出类型和参与者；
8. Trace、Pool Snapshot、报告和监控页展示画像版本、动作、工具、执行反馈和建议路由。

### 56.3 验证与边界

本地全量测试 209 项全部通过。当前 AutoGen 集成保持框架原生调度权，AgentLite 的 Capability Router 在 AutoGen 路径中为 advisory；框架无关 Runtime 可使用 active 模式。旧固定角色函数仅保留归档和 API 兼容，生产 AutoGen 路径不再调用。

## 57. v5.13t：能力身份归并与上下文不膨胀

### 57.1 对 v5.13s 的校正

openEuler 实验发现，能力画像事件会随 AutoGen 钩子重复写入，UUID 容器被误当作独立 Agent；部分短 MemoryView 和当前任务在加入视图头后反而增加 Token。这三个问题不改变动态能力画像方向，但会污染注册表、Trace 和成本指标。

### 57.2 实现

1. `capability_profile_updated` 只在首次观测或角色、工具、契约真正变化时写入；
2. 运行反馈继续由 `capability_profile_feedback` 独立记录；
3. `AgentName_<uuid>_<same-uuid>` 容器归并到逻辑 Agent，UUID 保存在 `instance_aliases`；
4. 画像区分 `business` 与 `system`，默认能力路由只选择业务 Agent；
5. Team、Manager、Runtime 不再混入业务 Agent 注册表；
6. MemoryView 和当前任务视图均执行 Token 级不膨胀选择；
7. 候选视图、实际视图、不膨胀回退次数和业务/系统画像数量进入 Trace 与报告。

### 57.3 可靠性边界

不膨胀回退只在候选视图与来源视图语义等价时发生，来源视图是候选信息的超集。Schema、消息类型保持、协作组隔离、记忆准入和必要记忆优先策略均未关闭。底层契约回退仍按安全消息类型逐项扩展，不以绕过守卫换取改写次数。

### 57.4 验收标准

1. 业务画像集合必须等于真实 Team 配置中的逻辑 Agent；
2. 画像事件不得随重复钩子调用线性增长；
3. `minimal_role_view_tokens <= memory_source_view_tokens`；
4. `current_task_role_view_tokens <= current_task_source_tokens`；
5. 质量、完整交付、通信、记忆、控制和重试成本继续按端到端口径联合判断。

## 58. v5.13u：透传分类与 MemoryView 采用证据

### 58.1 对 v5.13t 实验口径的校正

v5.13t 的 `rewrite_fallback_event_count` 同时包含无文本控制消息、成本门禁和真实错误，不能直接当作故障率。记忆指标也只覆盖检索与注入，尚未证明下游输出真正采用了记忆。

### 58.2 实现

1. 改写结果分为成功改写、无可改写内容透传、成本门禁透传、策略透传和真实错误回退；
2. 无文本 AutoGen 控制包装不再计入契约错误；
3. 只为实际进入 Agent Prompt View 的 MemoryRef 建立采用跟踪；
4. 输出完成后按通用事实规则排除当前任务自带事实并建立采用证据；
5. 采用事件记录 MemoryRef、事实指纹、匹配分数和归因算法；
6. 只有取得证据才回写 `useful_hit_count` 和 `memory_supported_output_count`；
7. 无证据保持 `unassessed`，错误或过期仍需独立可靠性证据。
8. 记忆槽位兜底推断只依据任务语义标签，不再依赖固定 Agent 角色名。

### 58.3 与最终创新方案的对应

该版本补齐 TLC-Memory 的“命中不等于有效复用”反馈闭环，同时保持 SHP 的 Schema、消息类型和成本门禁。算法不识别固定 Agent 角色或领域题目；动态能力画像仍决定角色视图，记忆归因只观察实际 MemoryView 与下游输出。

### 58.4 验收标准

1. `rewrite_error_fallback_count` 与正常透传分开统计；
2. `useful + wrong + unassessed = memory_injected`；
3. 至少一条采用证据可追溯到 MemoryRef 与事实指纹；
4. 三组使用相同任务、Agent、轮次和模型；
5. 质量、Provider Token 与端到端协作成本继续联合报告。

详细说明：`docs/experiments/v5.13u-passthrough-memory-adoption.md`。

## 42. v5.13p：AutoGen Studio 网页 Run 级绑定

### 42.1 解决的问题

`agentlite autogen -- autogenstudio ui ...` 启动的是一个长期运行的后端进程。旧实现只生成一个 AgentLite `launch_*` Session，因此网页中连续执行的多个 Run 会被汇总成一个任务，模型 Token、改写、状态和记忆事件无法逐次归属。

### 42.2 实现路径

1. 读取 AutoGen Studio 0.4.2.2 已有的 `RunContext.current_run_id()`，不修改 Studio 源码；
2. 使用 `ContextVar` 将 `framework_run_id` 传播到 Team 内的 Agent、Core、模型客户端和内核 Trace；
3. 将 Studio 原生 Run 编码为 `autogenstudio:<run_id>`；普通代码端 Team Run 使用 `autogen:<AgentLite call_id>`；
4. 识别直接启动命令中的 `--appdir`，只读查询 Studio 的 `run -> session -> team` 关系；
5. 每个 Run 写入独立 `autogen_driver/runs/<bounded_id>/run.json`，长逻辑 ID 通过稳定哈希映射到最长 16 字符的物理目录；
6. 新增 `agentlite report autogen-run`，只汇总一个 Run；
7. 监控接口新增 `/api/framework-runs` 和 Run 级 snapshot，任务列表优先显示独立 Run。

### 42.3 不改变的边界

本版本不改 Agent 角色、Team JSON、终止条件或领域交付规则，也不向 Studio 数据库写入数据。`agent_runtime` 中仍不存在 Question A 专用逻辑。若 Studio 版本未提供兼容的 `RunContext`，AgentLite 保持原有进程级观测，不伪造 Studio 原生 ID。

### 42.4 验证标准

同一 Python 进程连续执行两个模拟 Studio Run：

```text
autogenstudio:101
autogenstudio:102
```

必须分别产生开始事件、结束事件和 Run 清单；嵌套 AutoGen 事件不得缺少 `framework_run_id`；按 Run 导出的 Token 和事件计数不得包含另一 Run。

本机完整发行门禁 10 / 10 通过，189 项单元测试通过。门禁同时覆盖真实 AutoGen Team 接管、CLI 重写、不可变实验归档、Studio 双 Run 绑定、编译与差异检查。

## 51. v5.13m openEuler 24.03 LTS-SP3 兼容性验收（2026-07-20）

### 51.1 验收结果

在 openEuler 24.03 LTS-SP3、Python 3.11.6、AutoGen 0.7.5 环境中完成发行门禁：

```text
release_gate_report.passed: true
agentlite version: 0.5.13.dev0
doctor autogen: true
unittest: 174 tests OK
compileall: true
git diff --check: true
```

确定性 Team 基准保持调用方可见语义不变，同时把内部任务 Token 从 2,071 降到 1,069，把三接收者广播成本从 6,213 降到 918。真实 MiMo A1-A2 冒烟为 2/2 有效交付、21,216 LLM Token、0 次 Provider 重试，本地运行时开销约 0.27%。

### 51.2 结果边界

真实冒烟的第二次运行中，短消息加记忆视图后的候选成本高于原生消息，因此 2 次 Team 候选和 6 次 Agent 候选全部由成本门禁回退，实际传输节省为 0。该结果证明 openEuler 接管与安全回退可工作，不能作为真实业务 Token 已下降的证据。

归档中同时存在两次 session，原始 `agentlite-report.json` 对应第一次运行，而 `sequence_result.json` 和 `llm_usage_summary.json` 已被第二次运行覆盖。正式报告已按较新的 `launch_535a06b3acc14eccba9ce54dd713cdbd` 重新解析，避免混用 26,367 与 21,216 两套 Provider Token 口径。

完整报告：

```text
docs/experiments/v5.13m-openeuler-acceptance-20260720.md
```

## 52. v5.13n 上下文去重与改写结果口径（2026-07-21）

### 52.1 通用实现

Agent 输入与 Team 广播候选在追加共享记忆前，先比较 MemoryView 的事实正文是否已被当前任务、用户历史或最新上游成果覆盖。完全覆盖的记忆同时从 Prompt View 和 SHP `memory_refs` 移除；包含新事实或不同数字的记忆继续保留。规则使用本地文本归一化、事实片段和字符三元组覆盖，不调用 LLM，也不包含 Question A 领域逻辑。

### 52.2 指标校准

Session 报告新增：

```text
rewrite_audit_event_count
rewrite_costed_event_count
rewrite_applied_event_count
rewrite_fallback_event_count
rewrite_cost_gate_fallback_count
rewrite_contract_fallback_count
memory_candidate_deduplicated_count
memory_candidate_deduplicated_tokens
```

`actual_rewrite_event_count` 从 v5.13n 起只表示真正成功修改消息的次数。使用新报告器回放 openEuler 第二次会话得到：34 次审计、8 次有成本决策、0 次成功改写、34 次回退，其中 8 次成本门禁回退、26 次结构回退。

最终发行门禁 8 / 8 通过，177 项测试通过。确定性 Team 基准保持调用方可见 Token 为 2,071，同时把内部任务 Token 降至 1,042、三接收者广播成本降至 894，质量检查保持 12 / 12。

完整报告：

```text
docs/experiments/v5.13n-context-dedup-rewrite-metrics.md
```

## 53. v5.13o 不可变实验归档与精确用量绑定（2026-07-21）

### 53.1 实现

代码端实验新增 `experiment_run.json` 与 `experiment_result.json`，所有核心结果文件使用独占创建；重复使用目录会直接失败，旧 Token 日志不再被清空。AgentLite 的 `--experiment-dir` 会把本次 `session_id` 写入启动绑定，目标程序的 Provider 汇总同时写入 `run_id` 与 `session_id`。

启动结束后，AgentLite 会把精确 Session 复制到实验目录的 `agentlite_data/sessions/<session_id>`，校验 Provider 哈希和四处身份字段，并自动生成 JSON/Markdown Session 报告。比较器默认拒绝未绑定、身份错配、哈希不符或三组复用同一 `run_id` 的正式数据。

### 53.2 验证

新增真实 CLI smoke 覆盖首次启动、精确绑定、自包含 Session、自动报告、Provider 总量、Session 目录摘要、二次启动拒绝和原清单哈希不变。该 smoke 不调用外部 LLM，并已加入 release gate。最终 184 项测试通过，扩展后的发行门禁 9 / 9 通过。

完整报告：

```text
docs/experiments/v5.13o-immutable-experiment-binding.md
```

## 42. v5.13m 普通开发者有状态三组公平实验（2026-07-20）

本轮已在同一 MiMo 模型、同一 AutoGen Team、同一 A1-A10 连续任务链上完成：

```text
native   : 原生 AutoGen
observed : AgentLite 只观察、不改写
managed  : AgentLite 真实接管消息、状态与共享记忆
```

三组均为 10 / 10 严格最终交付。Provider 实际总 Token：

```text
native   : 517,444
observed : 549,227
managed  : 641,657
```

接管组真实通信审计结果：

```text
actual_native_transport_tokens   : 193,672
agentlite_runtime_tokens         : 154,876
actual_transport_token_savings   : 38,796
actual_transport_savings_ratio   : 20.03%
```

因此当前结论必须区分：

```text
已证明：AgentLite 真实接管链路中的协作载荷下降 20.03%。
未证明：Provider 实际总 Token 下降；本轮反而增加 24.01%。
```

接管组 A10 因 Reviewer 发现 2,640 元超过 2,600 元预算硬约束而进入第二个完整协作周期，并伴随 5 次 Provider 重试，是总 Token 与延迟增幅的主要来源。剔除 A10 后，接管组相对原生组仍增加 3.08% Provider Token，说明 AutoGen 历史、Prompt View 和记忆注入之间仍可能存在重复上下文。

质量评测已改为跨任务稳定匿名轨道：裁判同时读取匿名轨道的上一轮与当前交付物，分数冻结后才解盲。三个不同匿名排列的均值为：

```text
native   : 9.2 / 10
observed : 10.0 / 10
managed  : 9.7 / 10
```

该结果只支持“本轮没有观察到质量下降”，不支持统计意义上的质量提升。观察组不改写消息却获得最高分，说明单次模型生成和 LLM 裁判波动不可忽略。

下一步校准为：

```text
1. 消除 AutoGen 原生历史、Prompt View、共享记忆中的重复事实；
2. 建立记忆注入到下游输出引用的采用证据，填充 useful_memory_hit_count；
3. 在 A/B 两条连续任务上各做至少 3 个独立重复，再计算均值与标准差。
```

完整报告：`docs/experiments/v5.13m-fair-stateful-a1-a10-20260720.md`。

## 50. v5.13m 时序视图、事实依据与终止收口

### 50.1 对应既定方案

| v5.13m 实现 | 对应方案原则 |
|---|---|
| 当前任务、原始用户历史、最新上游成果分层组装 | SHP Prompt View 按接收者和任务裁剪 |
| 最新上游成果完整保留，较旧消息仅保留摘要 | 状态引用传递与低开销上下文重建 |
| 单一改写消息替代重复克隆 | 防止通信重复与 Token 口径膨胀 |
| 无依据用户确认断言被拒绝 | Contract Guard 与错误状态隔离 |
| 无依据最终候选不得进入长期记忆 | TLC-Memory 规则优先准入 |
| 明确最终成果边界后及时终止 | 避免无效协作轮次和错误扩散 |

### 50.2 工程边界与证据

运行时只核对消息时序、原始用户事实来源和通用最终成果边界，不包含 Question A 的地点、预算或行程字段。旅游专家提示词仍位于 experiments，三组实验共同使用。

v5.13l 已完成真实 MiMo A1-A3 接管实验；v5.13m 已完成 171 项自动化测试、旧真实输出离线回放和新的 MiMo A1-A3 接管组在线复验。最终在线结果为 3 / 3 协议有效、9 次 LLM 调用、51,572 LLM Token、无 Provider 重试和 11.11% 实际传输 Token 节省。该结果仍是接管组冒烟验证，不替代同版本三组盲评。

详细证据：

```text
docs/experiments/v5.13m-chronology-grounding-results.md
```

## 49. v5.13k 终止、记忆准入与成本口径对齐

### 49.1 对应既定方案

| v5.13k 实现 | 对应方案原则 |
|---|---|
| Team 最终标记成为长期记忆准入强条件 | 记忆候选池先行、规则优先、错误结果不得晋升 |
| Reviewer 否决意见保留为未验证候选 | 状态池、候选池、长期记忆池分层 |
| 精确末行终止替代字符串包含终止 | 协议守卫和错误输出隔离 |
| 直接消息、Prompt View、记忆读取互斥拆分 | 端到端成本不得发生重复记账或成本转移 |
| 外部 Provider usage 可合并进 Session 报告 | Provider Token 与协作 Token 分口径记录 |
| 三组统一 9 turn 和阶段化 Agent 提示词 | 公平实验边界与质量非劣化验证 |

### 49.2 工程边界

agent_runtime 只包含领域无关的终止、交付校验、记忆准入和成本统计机制，不包含 Question A 的候选地、预算、出发时间或旅行交付字段。

旅行专家角色提示词位于 experiments/ordinary-developer-autogen，仅作为普通开发者实验配置，原生组、观察组和管理组共同使用。

详细证据：

docs/experiments/v5.13k-termination-memory-accounting-root-cause.md

## 48. v5.13j 最终交付与成本口径校准

本次不新增方案，只补齐既定创新方案中此前实现不完整的四个守卫：

| v5.13j 实现 | 对应既定方案 |
|---|---|
| 完成标记后的通用审查稿识别 | Contract Guard / 最终交付完整性守卫 |
| 未校验 Team 输出先进入候选、规则拒绝长期入池 | TLC-Memory 规则优先准入 |
| 有记忆命中也必须通过 Token 成本比较 | CSCC 成本感知一致性控制 |
| 命中、注入、有效、错误、待评估分栏 | useful/wrong memory 指标校准 |
| actual、shadow、Provider 三种 Token 分栏 | 端到端成本与 cost shifting 防护 |

2026-07-18 P0 补齐：

```text
通用 FinalDeliveryGuard 只识别审查意见不等于最终交付，不包含 Question A 字段规则；
Question A/B Delivery Policy、领域记忆槽位和 benchmark Schema 不进入正式运行路径；
A2/A6/A8 等任务要求只作为离线盲评口径，不驱动重试、记忆准入或上下文改写；
Team 参与者与 Agent 输入共享同一 collaboration_group_id，MemoryView 能在 Agent 输入层检索；
记忆命中不再绕过 token_not_reduced，完整 Prompt View 不省 Token 时保持原生输入；
不合格审查稿只能进入低置信候选并由通用规则拒绝长期入池；任务语义正确性由盲评判断。
```

离线重放已取回的 A1-A10 结果：A1/A2/A6/A8 四个错误判断曾用于定位通用守卫缺陷，但这些题目规则现已退出运行时，只保留为盲评诊断。领域边界校准后全量回归为 159 项通过；真实 MiMo 三组实验仍需在本轮代码推送后重新运行，不能把离线重放表述为新的端到端实验结果。

当前 Driver phase：`v5.13j`。

详细证据见：

```text
docs/experiments/v5.13j-final-delivery-memory-cost-repair.md
```

## 47. v5.13i 补充：AutoGen 共享记忆桥接

版本归属：

```text
主版本：v5 跨框架适配与比赛验证版
当前 Driver phase：v5.13i
性质：把 AutoGen 接管链路接回既有 TLC-Memory 候选与规则准入机制
```

本项不引入新的小版本创新，也不改变三份最终创新方案的职责边界。它补齐的是工程接线：此前 v3.3 已实现 `State -> PromotionView -> MemoryCandidate -> rules-first admission -> MemoryView`，但 AutoGen Driver 尚未调用该路径。

已实现：

```text
AutoGen Agent 中间输出写入 StatePool 后生成 pending MemoryCandidate；
AutoGen Team 最终结果写入 StatePool 后生成候选，并可按规则准入为 MemoryView；
MemoryStore 持久化到 AgentLite data-dir；
后续独立 AutoGen 进程按工作区和 Team 成员签名检索；
MemoryView 通过 memory_ref 和裁剪视图注入 Team 入口；
共享记忆 token 单独计入 retrieved_memory_tokens；
不同 Team 使用严格作用域过滤；
shadow-only / rewrite off 模式不读写共享记忆。
```

与创新方案的对应关系：

| v5.13i 补充能力 | 对应方案模块 |
|---|---|
| 原始输出先写状态池 | SHP-State / 三层状态池 |
| 中间输出只进入候选池 | TLC-Memory / 记忆候选层 |
| 规则阈值决定最终准入 | Rules-first Memory Admission |
| MemoryView 跨进程复用 | TLC-Memory / 共享记忆 |
| `memory_ref` 随 SHP 传递 | SHP-Control + State/Memory 引用 |
| 检索 token 单独记账 | CSCC / 端到端成本防转移口径 |
| Team 签名严格隔离 | 作用域治理 / 防记忆污染 |

真实 AutoGen 0.7.5 双进程验证：

```text
passed: true
seed final admission_status: admitted
recall memory_hit_count: 1
recall useful_memory_hit_count: 1
retrieval event MemoryView tokens: 127
session retrieved_memory_tokens for 3 receivers: 381
recall task contains shared-memory marker: true
recall task contains seed fact: true
ordinary AutoGen script imports AgentLite: false
```

当前边界：

```text
只对 real-rewrite 接管模式启用；
Team 入口仍以受支持的 task: str 路径为主；
Team 成员签名变化会形成新隔离组；
共享记忆不替代角色提示词、Team 调度和终止条件；
真实 LLM 的质量提升仍需重新执行 Studio A1-A10 实验验证。
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

## 59. v5.13v：保守记忆归因、归一化成本与技术质量评审

v5.13v 不改变 AgentLite 的业务行为，重点修正 v5.13u 实验中暴露的三项证据缺口：当前任务事实可能被误归因为记忆贡献、额外协作轮次干扰总 Token 对比、主盲评对技术错误存在满分上限效应。

实现映射：

- `agent_runtime/drivers/autogen.py`：记忆归因升级为 `distinctive_fact_overlap_rules_v2`，记录当前任务来源和指纹，并在归因前排除当前任务已给出的事实；
- `web_monitor/parser.py` 与 `agent_runtime/eval/autogen_session_report.py`：汇总被排除的当前任务事实数和最终可归因事实数；
- `experiments/ordinary-developer-autogen/compare_stateful_runs.py`：新增相同逻辑调用归一化 Token 口径；
- `experiments/ordinary-developer-autogen/judge_stateful_technical_blind_batch.py`：新增独立匿名技术审查；
- `experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py`：合并主盲评和技术盲评，最终分取较低值；
- `experiments/v5.13v-conservative-evidence-quality/`：完整 openEuler 验收和不可变证据打包入口。

本阶段仍保持通用边界：核心运行时不认识 Question A/D，不固定 Agent 角色名，技术盲评只属于实验评测，不进入运行时，也不计入协作 Token。

详细说明：`docs/experiments/v5.13v-conservative-attribution-technical-quality.md`。

## 60. v5.13w：版本冲突感知记忆与污染归因

v5.13w 修复跨层历史污染：MemoryStore 已将旧 Claim 标为 `superseded`，但 AutoGen Agent 的原生消息历史仍可能保留旧值。该阶段不删除框架历史，而是向所有动态能力角色提供最小修订守卫，并在输出后同时校验当前事实采用与过期事实污染。

实现映射：

- `agent_runtime/memory/memory_store.py`：为存在历史 Claim 的 MemoryView 生成 `revision_guard`；Prompt 只携带当前有效值和版本策略，旧值仅供本地审计；持久化 useful、wrong、mixed 三类反馈计数；
- `agent_runtime/memory/context_views.py`：把修订守卫标为动态最小上下文的必选控制单元，不依赖固定角色名；
- `agent_runtime/drivers/autogen.py`：采用归因升级为 `active_and_historical_fact_rules_v3`，互斥输出 useful、wrong、mixed、unassessed；
- `agent_runtime/core/kernel.py` 与 `agent_runtime/eval/metrics.py`：写回和汇总四分类反馈；
- `web_monitor/parser.py` 与 `agent_runtime/eval/autogen_session_report.py`：展示 mixed 指标，并验证四类总数与实际注入数守恒；
- `experiments/v5.13w-conflict-aware-memory/`：提供不调用 LLM 的确定性门禁和 openEuler 真实 LLM 完整验收入口。

通用边界：

```text
agent_runtime 不认识 Question A/D；
修订守卫不依赖 Planner、Writer、Reviewer；
旧 Claim 原文不会重新注入 Agent Prompt；
只有共享概念锚点且结构化标量冲突时才执行负向污染判定；
明确否定旧值不会计为错误采用。
```

详细说明：`docs/experiments/v5.13w-conflict-aware-memory.md`。

## 61. v5.13x：事实级 Claim 与 MemoryView 校准

v5.13x 将 v5.13w 的“整篇交付 Claim + 文本冲突匹配”校准为最终创新方案规定的 TLC-Memory 事实级主线。运行时仍不识别具体问题、业务领域或固定 Agent 角色。

实现映射：

- `agent_runtime/memory/claim_extractor.py`：按“结构化 Claim 优先、显式键值和高置信规则补充”提取通用事实，不包含 Question A/D 词表；
- `agent_runtime/memory/schema_registry.py`：补齐 `scope`、`certainty`、`value_type`、单位、时效和 Slot 冲突策略；
- `agent_runtime/memory/memory_store.py`：admitted ClaimCandidate 逐条形成 `ccf.v2` ClaimCard，先写 `provisional_active`，再按 `subject + slot_id + scope + temporal_scope` 批量生成 MemoryView；
- `agent_runtime/memory/memory_store.py`：同值合并证据，显式修订形成 active/historical，无法裁决形成 conflicting 并阻断 Prompt；检索按 MemoryView 去重；
- `agent_runtime/memory/memory_store.py`：新增 `get_prompt_view()`、`get_audit_view()` 和 `expand_evidence()`，业务 Prompt 与审计溯源分离；
- `agent_runtime/memory/memory_store.py`：加载旧 `ccf.v1-lite` 整篇交付 Claim 时转为 `legacy_document_claim / legacy_audit`，不参与业务 Prompt 和负向污染；
- `agent_runtime/drivers/autogen.py`：采用评估升级为 `ccf_v2_semantic_key_value_rules`，按语义键、规范化值和极性区分 useful、wrong、mixed、unassessed；
- `agent_runtime/eval/metrics.py`：新增原始/临时 Claim、Slot 映射、冲突发现/裁决/未决和 active value 选择计数；
- `experiments/v5.13x-fact-level-memory/`：提供跨“系统配置”和“普通规划”两个领域的确定性验收及 openEuler 工程证据打包入口。

确定性验收：

```text
11/11 checks passed
244 unit tests passed in agentlite-autogen environment
```

当前可确认：

```text
整篇最终交付不再作为无条件 formal Claim fallback；
Prompt View 只暴露 active_value 和必要修订策略；
历史值只在 Audit/Evidence 展开中出现；
任意 Agent 名称共用同一事实、冲突和角色视图机制；
unresolved scope/conflict 不会进入业务 Prompt。
```

仍需下一阶段真实实验确认：

```text
Native / Observed / Managed 在相同 Team、调用上限下的 Provider Token；
端到端协作 Token、延迟和重试成本；
事实级分类人工抽查的假阳性/假阴性；
匿名质量评审是否相对 Native 显著下降。
```

详细设计：`docs/planning/v5.13x-事实级Claim与MemoryView校准.md`。

工程验收：`experiments/v5.13x-fact-level-memory/README.md`。

## 62. v5.13y：真实 AutoGen 事实级公平对照

v5.13y 不新增业务规则，负责把 v5.13x 的确定性机制放入真实 AutoGen 和真实
Provider 环境，完成 Native、Observed、Managed 三组公平实验。

实现映射：

- `web_monitor/parser.py`：从 `state_memory_bridge` 汇总原始 Claim、
  provisional Claim、Slot 映射、作用域、冲突和 active value 指标；
- `agent_runtime/eval/autogen_session_report.py`：把上述事实级指标加入会话报告，
  与 Provider Token、端到端协作 Token 和记忆采用指标并列展示；
- `experiments/ordinary-developer-autogen/compare_stateful_runs.py`：报告标题改为
  通用连续 AutoGen 对照，不再误写成 A1-A10；
- `experiments/v5.13s-dynamic-capability-acceptance/verify_acceptance.py`：支持
  `ccf_v2_semantic_key_value_rules`，并验证事实链路、语义键采用和四分类守恒；
- `experiments/v5.13y-real-autogen-fact-memory/`：提供 openEuler 一键运行、
  双盲质量评审、事实采用抽样和不可变证据打包；
- `fact_attribution_sample.csv`：保留空白人工标签，供后续逐条核验假阳性、
  假阴性和不确定样本，未填写前不冒充人工验收结论。

公平变量固定为同一任务序列、Team、Provider、模型参数、最大轮次和重试参数。
Observed 只测钩子观测开销，Managed 才启用真实改写和共享记忆。报告同时保留：

```text
Provider 实际 Token；
相同 task + agent + ordinal 的归一化公共调用 Token；
消息 + Prompt View + 记忆读取 + 控制 + 重试的端到端协作 Token；
匿名主评审与独立技术评审的较低分；
完整逐步消息与事实采用证据。
```

详细说明：`docs/experiments/v5.13y-real-autogen-fact-memory.md`。

运行入口：`experiments/v5.13y-real-autogen-fact-memory/run_openeuler.sh`。

## 63. v5.13z：错误记忆采用修复闭环

v5.13z 解决 v5.13y 暴露的实际缺口：运行时已经能够识别 Agent 输出采用了
过期事实，但原先只在输出传播后记录 `wrong/mixed`，不能阻止旧事实进入下游。

实现映射：

- `agent_runtime/reliability/memory_adoption_guard.py`：新增规则优先的通用采用后
  守卫；只在结构化证据定位到历史标量和输出片段时精确替换；
- `agent_runtime/drivers/autogen.py`：在 `on_messages` 和
  `on_messages_stream` 的完整消息离开 Agent 前执行守卫，同时保留修复前正文
  供采用审计；
- `agent_runtime/core/kernel.py`：无法安全修复时写入 `failure_state`，
  `allowed_next_step=review_or_retry_only`，并禁止该结果进入记忆候选流程；
- `agent_runtime/memory/memory_store.py`：发布
  `downstream_fact_correction` 补偿事件；当前 MemoryView 正确时不错误执行
  `soft_deprecate`；
- `web_monitor/parser.py` 与
  `agent_runtime/eval/autogen_session_report.py`：单独展示规则修复、阻断、执行
  失败和已修复事实数量；
- `experiments/v5.13z-memory-adoption-repair/`：提供领域无关、角色名无关的
  确定性门禁和 openEuler 证据打包入口。

该实现遵守最终创新方案的边界：规则优先；不确定冲突交给 Reviewer 或有限重试；
不硬回滚已经发生的下游执行；不删除历史证据；业务 Agent 不直接面对未裁决的新旧
事实；修复事件与原始错误采用均可审计。

详细说明：`docs/experiments/v5.13z-memory-adoption-repair.md`。

工程验收：`experiments/v5.13z-memory-adoption-repair/README.md`。

## 64. v5.14a：真实 AutoGen 记忆事实故障注入

v5.14a 不新增业务领域规则，也不修改生产运行时的角色识别。该阶段把 v5.13z 已实现的
错误记忆采用守卫放入真实 AutoGen GroupChat 和真实 Provider 输出链中，验证修复或阻断
是否发生在下游 Agent 接收之前。

实现映射：

- `experiments/v5.14a-real-memory-fault-injection/fault_matrix.json`：定义正确当前值、
  仅旧值、新旧混合、明确否定旧值、无关字段同值和旧版非结构化冲突六类通用场景；
- `seed_fault_memory.py`：按真实 Team 参与者计算严格协作组 ID，在隔离 scope 中写入
  CCF v2 与 CCF v1-lite 的活动/历史值，并重载校验持久化结果；
- `fault_injection_app.py`：使用真实 AutoGen `RoundRobinGroupChat`，由 `FaultEmitter`
  调用真实 LLM，`DownstreamProbe` 确定性记录框架实际传播的消息；
- `verify_fault_injection.py`：把 Provider 原文指纹与
  `autogen_memory_adoption_guard` trace 对齐，统计故障注入率、Native/Managed 下游逃逸率、
  安全场景误报率、补偿事件和无关字段保留情况；
- `run_openeuler.sh`：运行确定性前置门禁、Native 控制、隔离记忆预置、Managed 接管、
  逐行验收、全量单元测试、编译检查和不可变证据打包。

公平边界：

```text
Native 与 Managed 使用相同模型、温度、故障矩阵和重复次数；
Provider 未实际产生危险旧值时，验收失败，不能冒充守卫成功；
否定旧值场景至少保留一条真实否定样本，其他仅输出活动值且未触发守卫的样本记为安全退化；
DownstreamProbe 不调用 LLM，只记录 AutoGen 实际传播内容；
短故障输出的 Token 只用于审计，不用于宣称通信成本下降；
生产 agent_runtime 不识别 v5.14a 场景，也不依赖固定 Agent 角色名称。
```

真实 openEuler 实验与不可变证据复核结果见：
`docs/experiments/v5.14a-real-memory-fault-injection-results-20260724.md`。

详细说明：`docs/experiments/v5.14a-real-memory-fault-injection.md`。

工程验收：`experiments/v5.14a-real-memory-fault-injection/README.md`。

## 65. v5.14b：真实 AutoGen 公平成本-质量预检

v5.14b 不继续增加生产守卫，而是执行 v5.13x 已明确要求的真实
Native/Observed/Managed 联合验收。该阶段首先用 A1-A3 与 B1-B3 校验完整证据链，避免
直接运行双领域十轮正式实验后才发现成本或质量口径缺失。

新增：

- `experiments/v5.14b-fair-cost-quality-preflight/preregistration.json`：在 Provider
  调用前冻结成本、质量、交付和 fallback 阈值；
- `question_A_preflight.json`：旅行规划三轮连续任务；
- `question_B_preflight.json`：自包含本地资料快照的合成安全审计三轮连续任务；
- `agent_config_B.json`：只属于实验的合成安全审计专业 Agent 配置；
- `run_openeuler.sh`：依次运行 Native、Observed、Managed，导出绑定报告，完成双重匿名
  质量评分并打包证据；
- `verify_preflight.py`：联合校验 Provider 实际 Token、端到端协作 Token、匿名质量、
  完整交付、真实消息改写、共享记忆注入、真实非文本状态和错误 fallback；
- AutoGen session/run 报告新增 `state_summary`，直接统计 StatePool 中的状态类型、
  载荷类型、embedding 引用和实际字节数，不从任务文本推断状态是否存在。

公平性边界：

```text
三组使用相同任务、Agent、模型、temperature=0 和 max_turns=9；
不限制 completion Token；
A/B Managed 使用不同记忆作用域；
评审模型 Token 单独记录，不进入运行时协作成本；
Provider 实际 Token 与传输协作 Token 必须同时报告；
非文本状态必须来自 StatePool 快照；未真实生成的 retrieval/embedding 类型保持为 0；
领域提示词和资料快照只在 experiments 中，agent_runtime 不识别 Question A/B；
预检阈值不能在看到结果后修改并重新解释同一证据。
```

阶段说明：`docs/experiments/v5.14b-fair-cost-quality-preflight.md`。

运行说明：`experiments/v5.14b-fair-cost-quality-preflight/README.md`。

## 66. v5.14c：语义去重、最小能力视图与协议隔离

v5.14c 针对 v5.14b 真实预检暴露的 Provider Prompt 膨胀和证据重复问题进行通用修复。
该阶段不增加 Question A/B 业务规则，不固定 Agent 角色，不限制 completion 长度，也
不通过减少 AutoGen 协作轮次制造成本优势。

实现映射：

- `agent_runtime/state/state_pool.py`：新增稳定语义身份、并发原子去重、复用来源谱系和
  物理/逻辑状态计数；
- `agent_runtime/drivers/autogen.py`：过滤纯 AutoGen 路由元数据；按动态能力画像压缩
  chronology；SHP wire 只进入 trace，不进入 Agent 模型正文；以内容指纹避免重复改写；
- `agent_runtime/memory/memory_store.py`：按 CCF v2 语义键、规范值、极性和时效范围执行
  跨候选事实去重，并把新证据合并到已有记忆；
- `agent_runtime/memory/context_views.py`：Prompt View 不再暴露 consumer、profile、
  state 和 claim 等内部标识，只保留当前任务所需事实；
- `agent_runtime/eval/metrics.py`、`web_monitor/parser.py` 和
  `agent_runtime/eval/autogen_session_report.py`：新增状态复用、记忆准入去重、证据
  合并、路由误入池和模型可见协议标记指标。

通用边界：

```text
完整原文仍保存在冷层审计载荷；
最小视图由 CapabilityProfile 和语义动作生成，不由 Agent 名称生成；
短期事实不跨任务合并，跨任务/持久事实才可复用；
SHP StateRef/MemoryRef 仍存在于真实运行时和 trace；
模型正文及最终交付不得出现 AgentLite wire 协议字段。
```

本地回归共 102 项相关测试通过，`compileall` 与 `git diff --check` 通过。真实 Provider
成本目标尚未重跑，不据本地机制测试宣称 15% 目标已经达成。

详细说明：`docs/experiments/v5.14c-semantic-dedup-context-hygiene.md`。
