# 普通开发者 AutoGen + AgentLite 实验包

这个文件夹用于模拟普通开发者真实使用 AgentLite 的方式：

1. 代码端：开发者正常写 AutoGen Python 程序，然后只在启动命令前加 `agentlite autogen --`。
2. 网页端：开发者打开 AutoGen Studio 原生网页，手动配置 Agent 和 Team，然后用 AgentLite 接管 Studio 后端进程。

## 文件说明

| 文件 | 作用 |
|---|---|
| `code_app.py` | 普通开发者风格的 AutoGen 多 Agent 代码端样例 |
| `agent_config.json` | Planner / Writer / Reviewer 三个 Agent 的配置 |
| `question_A_sequence.json` | A1-A10 结构化连续任务；由同一个有状态 Team 依次执行 |
| `studio_agent_prompts.md` | AutoGen Studio 网页端手动配置 Agent 时可复制的提示词 |
| `studio_team_config.template.json` | AutoGen Studio 可导入的 Team 配置模板，不包含真实 API Key |
| `question_A.md` | A 组实验问题 |
| `question_B.md` | B 组实验问题 |

## 环境变量

以 MiMo OpenAI-compatible 接口为例：

```bash
export OPENAI_API_KEY="你的 API key"
export OPENAI_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1"
export OPENAI_MODEL="mimo-v2.5"
export OPENAI_MAX_RETRIES="4"
export OPENAI_RETRY_BACKOFF_SECONDS="2"
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="你的 API key"
$env:OPENAI_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1"
$env:OPENAI_MODEL="mimo-v2.5"
$env:OPENAI_MAX_RETRIES="4"
$env:OPENAI_RETRY_BACKOFF_SECONDS="2"
```

## 代码端实验

正式代码端实验使用 `question_A_sequence.json`。程序只创建一次
`RoundRobinGroupChat`，随后连续调用十次 `team.run()`；Agent 自己保存已经收到的
消息，不会在任务之间调用 `reset()`。原生、观察和接管三组使用完全相同的程序、
问题、Agent 配置和终止条件，只有启动方式不同。

每次正式实验先生成全新的编号：

```bash
export EXP_ID="$(date +%Y%m%d-%H%M%S)"
export RUN_ROOT="runs/ordinary-developer/v5.13o-${EXP_ID}"
export TRACE_ROOT=".agentlite-exp/v5.13o-${EXP_ID}"
mkdir -p "$RUN_ROOT" "$TRACE_ROOT"
```

### 原生有状态组

```bash
python experiments/ordinary-developer-autogen/code_app.py \
  --question-sequence-file experiments/ordinary-developer-autogen/question_A_sequence.json \
  --agent-config experiments/ordinary-developer-autogen/agent_config.json \
  --experiment-mode native \
  --output-dir "$RUN_ROOT/native"
```

### AgentLite 观察组

```bash
agentlite autogen \
  --data-dir "$TRACE_ROOT/observed" \
  --experiment-dir "$RUN_ROOT/observed" \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-sequence-file experiments/ordinary-developer-autogen/question_A_sequence.json \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --experiment-mode observed \
    --output-dir "$RUN_ROOT/observed"
```

### AgentLite 正式接管组

```bash
export AGENTLITE_MEMORY_SCOPE="code-stateful-A-${EXP_ID}"
agentlite autogen \
  --data-dir "$TRACE_ROOT/managed" \
  --experiment-dir "$RUN_ROOT/managed" \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-sequence-file experiments/ordinary-developer-autogen/question_A_sequence.json \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --experiment-mode managed \
    --output-dir "$RUN_ROOT/managed"
```

每次正式重复实验必须更换 `AGENTLITE_MEMORY_SCOPE`，并使用新的 `--data-dir`、
`--experiment-dir` 和 `--output-dir`。其中 `--experiment-dir` 必须与目标程序的
`--output-dir` 相同。新版本会拒绝任何已包含实验文件的目录，避免旧任务记忆污染 A1，
也避免历史 Token 和答案被静默覆盖。

### AgentLite 报告

使用 `--experiment-dir` 后，观察组和接管组结束时会自动生成
`agentlite_session_report.json` 与 `agentlite_session_report.md`，不再需要通过
`--session-id latest` 手工猜测本次 Session。需要重新查看时可以直接从绑定归档输出到终端：

```bash
agentlite report autogen-session \
  --experiment-dir "$RUN_ROOT/observed" \
  --format markdown

agentlite report autogen-session \
  --experiment-dir "$RUN_ROOT/managed" \
  --format markdown
```

每组都会生成：

| 输出 | 作用 |
|---|---|
| `llm_usage.jsonl` | 每次模型调用的 provider usage、耗时、任务和 Agent |
| `llm_usage_summary.json` | 全局、逐任务和逐 Agent 的实际 LLM Token 汇总 |
| `experiment_run.json` | 不可变 `run_id`、Session、模型和输入文件哈希 |
| `experiment_result.json` | 完成状态、Provider 用量哈希和核心产物哈希 |
| `agentlite_session_binding.json` | 观察/接管组的精确 AgentLite Session 绑定 |
| `agentlite_data/sessions/...` | 观察/接管组本次 Session 的自包含快照 |
| `agentlite_session_result.json` | 绑定校验结果、Session 摘要和自动报告哈希 |
| `sequence_result.json` | A1-A10 问题、完整消息、终止原因和最终交付状态 |
| `tasks/A*/final_answer.md` | 去除终止标记后的用户可见最终答案 |
| `quality_blind_candidates.json` | 不带实验组名称的质量盲评候选 |
| `quality_blind_mapping.json` | 盲评编号与任务、实验组的映射，仅在评分后使用 |

三组都运行完成后，自动核对任务并生成真实 Token 对比和统一盲评包：

```bash
python experiments/ordinary-developer-autogen/compare_stateful_runs.py \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --output-dir "$RUN_ROOT/comparison"
```

在完成质量盲评前，只能查看 `quality_blind_batch.json`，不要打开
`quality_blind_mapping.json`。汇总器不会仅凭 Token 较低就宣布 AgentLite 获胜；
正式结论必须同时满足严格最终交付和质量不降低。

质量盲评采用 10 分制，并在打开映射文件前冻结分数。匿名包为每个实验组生成
跨任务稳定的 `track_id`，并向裁判提供该匿名轨道的上一轮交付物；这样可以在不暴露
`native / observed / managed` 身份的前提下，真正判断目的地、预算口径和修订是否连续：

- 任务完成度 `0-4`：是否直接交付本轮要求的清单、行程、预算或最终手册；
- 上下文保持 `0-3`：是否保留此前已经确认的约束和修改；
- 正确性与一致性 `0-2`：事实、预算算术和内部表述是否一致；
- 清晰度与可执行性 `0-1`：是否能够被用户直接理解和执行；
- 若只输出审查意见或流程说明，没有实际交付物，总分最高 `4` 分。

`compare_stateful_runs.py` 只负责核对三组实验条件、汇总真实 Provider Token，并生成匿名候选包；
它不会自动伪造质量分数。评分者先读取 `quality_blind_batch.json`，记录候选分数和
`delivery_complete`，冻结后再打开 `quality_blind_mapping.json` 解盲并按组汇总均分。

可复现的 LLM 盲评与解盲命令：

```bash
python experiments/ordinary-developer-autogen/judge_stateful_blind_batch.py \
  --batch runs/.../comparison/quality_blind_batch.json \
  --output runs/.../comparison/quality_blind_scores_frozen.json

python experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py \
  --scores runs/.../comparison/quality_blind_scores_frozen.json \
  --mapping runs/.../comparison/quality_blind_mapping.json \
  --output runs/.../comparison/quality_unblinded_summary.json
```

裁判 Token 是离线评测成本，不属于运行时协作成本。正式报告应使用多个匿名排列重复评分，
并披露评分波动，不能挑选最有利的一次结果。

每一步可见的 Planner、Writer、Reviewer 输出都保存在 `sequence_result.json` 的任务消息列表和
`tasks/A*/run_result.json` 中。实验程序不会因为内容质量不合格而追加 Agent 轮次或额外 LLM 调用；
不合格输出按原样保存并标记为 `task_failed`。
`llm_usage.jsonl` 只保存逐次调用的 Provider Token、耗时、重试次数和 Agent 等元数据，不保存完整
Prompt 或隐藏推理过程。`quality_blind_candidates.json` 只保存面向用户的最终答案，不包含中间输出。

单任务调试仍可使用 `--question` 或 `--question-file`。正式 A 组性能实验必须使用
`--question-sequence-file`，否则不会形成有状态的十轮上下文。

## AutoGen Studio 原生网页实验

原生启动：

```bash
autogenstudio ui --port 8081 --appdir runs/ordinary-developer/studio-native-app
```

浏览器打开：

```text
http://127.0.0.1:8081
```

在 AutoGen Studio 原生网页里：

1. 配置模型。
2. 创建 Planner、Writer、Reviewer 三个 Agent。
3. 使用 `studio_agent_prompts.md` 中的提示词。
4. 创建 RoundRobinGroupChat。
5. 在 Playground 输入 `question_A.md` 或 `question_B.md` 的完整问题并运行。

也可以导入 `studio_team_config.template.json`。导入前需要在本地把三处 `REPLACE_WITH_YOUR_API_KEY` 替换为实验 Key，或导入后在 Studio 中重新绑定已配置的模型；不要把包含真实 Key 的 Team 导出文件提交到 Git。

Team 统一运行最多 9 个 turn，即三个完整的 Planner、Writer、Reviewer 周期。原生组、观察组和接管组
使用同一 Agent 配置、同一模型、同一轮次上限和 `ReviewerFinalTextTermination`。该终止器只接受 Reviewer
最后一行的精确完成标记，不会因为审查意见在正文中提到标记而提前结束。若到达上限仍未
形成合格交付，实验只记录 `delivery_valid=false` 和 `delivery_status=task_failed`，不会自动增加轮次或生成修复答案。

AgentLite 观察不改写 Studio 后端：

```bash
agentlite autogen \
  --data-dir .agentlite-exp/studio-observed-A \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- autogenstudio ui \
    --port 8081 \
    --appdir runs/ordinary-developer/studio-observed-app-A
```

AgentLite 正式接管 Studio 后端：

```bash
agentlite autogen \
  --data-dir .agentlite-exp/studio-agentlite-A \
  -- autogenstudio ui \
    --port 8081 \
    --appdir runs/ordinary-developer/studio-agentlite-app-A
```

AutoGen Studio 会把网页输入包装为 `Sequence[ChatMessage]`。请确保使用包含 Studio 消息序列兼容修复的最新 AgentLite，并在完整 A1-A10 前先运行 A1、A2，确认第二个 Run 的 `memory_hit_count > 0`。Studio 页面固定显示的“Run 之间不共享数据”提示不会因 AgentLite 接管而消失，不能用它判断共享记忆是否生效。

最新版本会把内部重写协议与 Studio 展示隔离。Team 内部仍接收 StateRef 和 MemoryView，但 Agent Steps、流式首条用户消息及最终 `TaskResult` 只显示原始问题，不应再出现 `AGENTLITE_TEAM_REAL_REWRITE v1`。内部接管证据保留在 `autogen_team_input_real_rewrite`、`autogen_memory_retrieval` 和 `autogen_team_display_restored` 事件中。

导出报告示例：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/studio-agentlite-A \
  --session-id latest \
  --format markdown \
  --output runs/ordinary-developer/studio-agentlite-A-session-report.md
```

## 记录口径

| 指标 | 含义 | 来源 |
|---|---|---|
| provider token | 模型 API 实际返回的 usage token | AgentLite provider usage hook / Studio 页面 / 模型后台 |
| collaboration token | Agent 间协作消息传递成本 | AgentLite session report |
| final quality | 最终答案质量 | `final_answer.md` 或 Studio Playground 输出 |

注意：浏览器页面本身不是可靠的数据源。AutoGen Studio 网页负责模拟真实用户操作，AgentLite report 负责结构化实验数据。

正式对比中唯一允许变化的是 AgentLite 的底层观察或通信改写状态。Question A/B 的字段要求只用于实验结束后的盲评，不注入运行时、状态池或记忆池。
