# 普通开发者视角的 AutoGen + AgentLite 实验步骤

本文档只覆盖两类真实用户场景：

1. 代码端：开发者自己写普通 AutoGen Python 程序，然后用 AgentLite 接管启动。
2. 网页端：开发者使用 AutoGen Studio 原生网页配置 Agent 和 Team，然后用 AgentLite 接管 Studio 后端启动。

这里不使用自建网页，因为自建网页只能证明“某个 Python Web 后端可以被接管”，不能代表 AutoGen Studio 原生网页体验。

## 1. 代码端实验

代码端样例：

```text
examples/developer_autogen_code_app.py
examples/developer_agent_config.json
```

它是普通开发者风格的 AutoGen 多 Agent 应用，不导入 `agent_runtime`，也不调用 AgentLite API。

### 1.1 原生运行

```bash
python examples/developer_autogen_code_app.py \
  --question-file docs/problems/A.md \
  --agent-config examples/developer_agent_config.json \
  --output-dir runs/developer-code/native
```

输出：

```text
runs/developer-code/native/final_answer.md
runs/developer-code/native/run_result.json
runs/developer-code/native/llm_usage.jsonl
runs/developer-code/native/llm_usage_summary.json
```

`llm_usage_summary.json` 记录模型服务返回的真实 usage，包括：

```json
{
  "llm_prompt_tokens": 0,
  "llm_completion_tokens": 0,
  "llm_total_tokens": 0
}
```

### 1.2 AgentLite 观察不改写

```bash
agentlite autogen \
  --data-dir .agentlite-exp/code-observed \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python examples/developer_autogen_code_app.py \
    --question-file docs/problems/A.md \
    --agent-config examples/developer_agent_config.json \
    --output-dir runs/developer-code/observed
```

导出报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/code-observed \
  --session-id latest \
  --format markdown \
  --output runs/developer-code/observed/agentlite_session_report.md
```

### 1.3 AgentLite 正式接管

```bash
agentlite autogen \
  --data-dir .agentlite-exp/code-agentlite \
  -- python examples/developer_autogen_code_app.py \
    --question-file docs/problems/A.md \
    --agent-config examples/developer_agent_config.json \
    --output-dir runs/developer-code/agentlite
```

导出报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/code-agentlite \
  --session-id latest \
  --format markdown \
  --output runs/developer-code/agentlite/agentlite_session_report.md
```

## 2. 网页端实验：AutoGen Studio 原生网页

AutoGen Studio 是 AutoGen 官方提供的低代码网页界面。普通用户会在网页中创建 Agent、Team、模型配置和任务，并在 Playground 中运行。

AgentLite 不应该要求用户改 Studio 前端，也不应该要求用户手动改 AutoGen Studio 源码。正确接入点是 Studio 后端 Python 进程。

### 2.1 原生 AutoGen Studio

安装并启动 AutoGen Studio：

```bash
pip install -U autogenstudio
autogenstudio ui --port 8081 --appdir runs/autogen-studio/native-app
```

浏览器打开：

```text
http://127.0.0.1:8081
```

在 Studio 网页中完成：

1. 配置模型 API key、base_url 和 model。
2. 创建 Planner、Writer、Reviewer 等 Agent。
3. 创建 RoundRobinGroupChat 或其他 Team。
4. 在 Playground 输入 A/B 题任务并运行。
5. 保存最终回答、Studio 页面截图和 Studio 自带 metrics。

原生 Studio 组的限制：

- Studio 页面可能显示 token usage，但它不一定稳定导出结构化文件。
- AgentLite 没有参与启动时，无法生成 AgentLite trace。
- 因此原生 Studio 组主要用于记录用户体验、最终答案质量、Studio 页面可见 token 或模型服务后台账单。

### 2.2 AgentLite 观察不改写 Studio 后端

关闭原生 Studio 后，使用 AgentLite 包住同一个 Studio 启动命令：

```bash
agentlite autogen \
  --data-dir .agentlite-exp/studio-observed \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- autogenstudio ui \
    --port 8081 \
    --appdir runs/autogen-studio/observed-app
```

浏览器仍然打开：

```text
http://127.0.0.1:8081
```

用户操作方式不变，仍然在 AutoGen Studio 原生网页里配置 Agent 和运行任务。

运行结束后导出 AgentLite 报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/studio-observed \
  --session-id latest \
  --format markdown \
  --output runs/autogen-studio/observed-agentlite-session-report.md
```

这一组用于回答：

- AutoGen Studio 后端是否成功被 AgentLite 注入。
- 原生 AutoGen 协作通信 token 估计是多少。
- Studio 里配置的 Agent/Team 是否仍能正常运行。

### 2.3 AgentLite 正式接管 Studio 后端

```bash
agentlite autogen \
  --data-dir .agentlite-exp/studio-agentlite \
  -- autogenstudio ui \
    --port 8081 \
    --appdir runs/autogen-studio/agentlite-app
```

浏览器仍然打开：

```text
http://127.0.0.1:8081
```

用户仍然只在 AutoGen Studio 原生网页中操作。

运行结束后导出报告：

```bash
agentlite report autogen-session \
  --data-dir .agentlite-exp/studio-agentlite \
  --session-id latest \
  --format markdown \
  --output runs/autogen-studio/agentlite-session-report.md
```

## 3. 网页端 token 到底怎么统计

网页端 token 不能从浏览器页面本身可靠统计。浏览器只知道前端展示了什么，不知道 AutoGen Studio 后端内部：

- 哪个 Agent 调用了模型；
- 哪次调用返回了多少 prompt/completion token；
- GroupChat 是否发生广播；
- 每条消息实际传给了几个 Agent；
- AgentLite 是否把全文替换成了 StateRef 和 Prompt View。

所以需要分成两类指标。

| 指标 | 中文解释 | 记录位置 |
|---|---|---|
| provider token | 模型服务实际计费 token，也就是 API 返回 usage 或后台账单 | AgentLite provider usage hook、Studio 自带 metrics、模型服务后台 |
| collaboration token | Agent 之间协作传递的消息 token，包括原生广播、AgentLite 短消息、Prompt View | AgentLite trace 和 `agentlite_session_report.md` |

当前最稳的实验口径是：

1. 原生 Studio 组：记录 Studio 页面 metrics、模型服务后台 usage、最终答案质量。
2. AgentLite observed 组：记录 Studio 页面 metrics，加 AgentLite 原生协作通信估计。
3. AgentLite takeover 组：记录 Studio 页面 metrics，加 AgentLite 接管后的协作通信 token。

当前工程已增加 `provider usage hook`，AgentLite 注入进程后会尝试捕获 AutoGen 模型客户端返回的 `RequestUsage(prompt_tokens, completion_tokens)` 或 OpenAI-compatible `usage` 字段，并写入 `agentlite_session_report.md`。

如果某个模型客户端不返回 usage，仍需要保存模型服务后台账单或 Studio 页面截图作为旁证。

## 4. 为什么不能只靠 AutoGen Studio 页面

AutoGen Studio 的 Playground 适合用户观察运行过程，但比赛报告需要结构化、可复现的数据。

页面显示值的问题是：

- 可能不能批量导出；
- 不一定能按 Agent、任务、轮次拆分；
- 不一定能区分 provider token 和 collaboration token；
- 不能展示 AgentLite 接管前后的 StateRef、Prompt View、fallback、schema_valid 等内部证据。

因此 Studio 页面负责“普通用户体验”，AgentLite 报告负责“系统实验数据”。

## 5. 建议记录表

| 场景 | 组别 | 启动方式 | provider token | 原生协作 token | AgentLite 协作 token | 最终质量 | 备注 |
|---|---|---|---:|---:|---:|---|---|
| 代码端 | native | `python app.py` | `llm_usage_summary.json` | - | - |  |  |
| 代码端 | observed | `agentlite autogen --rewrite off -- python app.py` | `llm_usage_summary.json` | report | - |  |  |
| 代码端 | agentlite | `agentlite autogen -- python app.py` | `llm_usage_summary.json` | - | report |  |  |
| Studio 网页端 | native | `autogenstudio ui ...` | Studio/后台 | - | - |  |  |
| Studio 网页端 | observed | `agentlite autogen --rewrite off -- autogenstudio ui ...` | Studio/后台 | report | - |  |  |
| Studio 网页端 | agentlite | `agentlite autogen -- autogenstudio ui ...` | Studio/后台 | - | report |  |  |

## 6. 当前能力与后续补充

当前已经具备：

1. `agentlite autogen -- autogenstudio ui ...` 包住 AutoGen Studio 后端进程。
2. AutoGen model client usage hook，捕获 `CreateResult.usage` 或 OpenAI-compatible `usage`。
3. `agentlite report autogen-session` 汇总 provider token 与 collaboration token。

后续还可以增强：

1. `agentlite autogen-studio` 便捷命令，内部等价于 `agentlite autogen -- autogenstudio ui ...`。
2. 在监控页面中按 session/task/agent 展示 Studio 运行期间的 token 明细。
3. 对不暴露 usage 的模型客户端增加事件日志或 HTTP 代理兜底采集。

其中 provider usage hook 是解决“AutoGen Studio 原生网页无法稳定导出 token”的核心；便捷命令和监控展示属于用户体验增强。
