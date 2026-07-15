# 普通开发者 AutoGen + AgentLite 实验包

这个文件夹用于模拟普通开发者真实使用 AgentLite 的方式：

1. 代码端：开发者正常写 AutoGen Python 程序，然后只在启动命令前加 `agentlite autogen --`。
2. 网页端：开发者打开 AutoGen Studio 原生网页，手动配置 Agent 和 Team，然后用 AgentLite 接管 Studio 后端进程。

## 文件说明

| 文件 | 作用 |
|---|---|
| `code_app.py` | 普通开发者风格的 AutoGen 多 Agent 代码端样例 |
| `agent_config.json` | Planner / Writer / Reviewer 三个 Agent 的配置 |
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

原生运行：

```bash
python experiments/ordinary-developer-autogen/code_app.py \
  --question-file experiments/ordinary-developer-autogen/question_A.md \
  --agent-config experiments/ordinary-developer-autogen/agent_config.json \
  --output-dir runs/ordinary-developer/code-native-A

  python experiments/ordinary-developer-autogen/code_app.py \
  --question-file experiments/ordinary-developer-autogen/question_B.md \
  --agent-config experiments/ordinary-developer-autogen/agent_config.json \
  --output-dir runs/ordinary-developer/code-native-B
```

AgentLite 观察不改写：

```bash
agentlite autogen \
  --data-dir .agentlite-exp/code-observed-A \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-file experiments/ordinary-developer-autogen/question_A.md \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --output-dir runs/ordinary-developer/code-observed-A



agentlite autogen \
  --data-dir .agentlite-exp/code-observed-B \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-file experiments/ordinary-developer-autogen/question_B.md \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --output-dir runs/ordinary-developer/code-observed-B
```

导出观察报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/code-observed-A \
  --session-id latest \
  --format markdown \
  --output runs/ordinary-developer/code-observed-A/agentlite_session_report.md

agentlite report autogen-session \
  --data-dir .agentlite-exp/code-observed-B \
  --session-id latest \
  --format markdown \
  --output runs/ordinary-developer/code-observed-B/agentlite_session_report.md
```

AgentLite 正式接管：

```bash
agentlite autogen \
  --data-dir .agentlite-exp/code-agentlite-A \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-file experiments/ordinary-developer-autogen/question_A.md \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --output-dir runs/ordinary-developer/code-agentlite-A

agentlite autogen \
  --data-dir .agentlite-exp/code-agentlite-B \
  -- python experiments/ordinary-developer-autogen/code_app.py \
    --question-file experiments/ordinary-developer-autogen/question_B.md \
    --agent-config experiments/ordinary-developer-autogen/agent_config.json \
    --output-dir runs/ordinary-developer/code-agentlite-B
```

导出接管报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/code-agentlite-A \
  --session-id latest \
  --format markdown \
  --output runs/ordinary-developer/code-agentlite-A/agentlite_session_report.md


agentlite report autogen-session \
  --data-dir .agentlite-exp/code-agentlite-B \
  --session-id latest \
  --format markdown \
  --output runs/ordinary-developer/code-agentlite-B/agentlite_session_report.md
```

B 组实验只需要把命令中的 `question_A.md` 和输出目录改成 `question_B.md` / `B`。

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

新版 Team 最多运行 6 轮。前三轮完成规划、草案和首次审查；若 Reviewer 发现重大问题，则后三轮用于修订。首次审查合格时会立即输出完整答案并结束，不会强制消耗六轮。

Team 使用 AgentLite 提供的严格终止条件：只有 `reviewer` 的可见文本最后一行精确等于 `FINAL_ANSWER_READY` 才会结束。Planner、Writer、Reviewer 的模型思考过程、审查意见正文或其他 Agent 偶然提到该字符串都不会触发终止，避免把“退回修改”误当成最终交付。

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
