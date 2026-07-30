# AgentLite

AgentLite 是面向多智能体低开销协作的跨框架运行时工具层。它在不要求
用户程序显式导入 AgentLite 的情况下，为现有 AutoGen 应用增加结构化
状态传递、受治理的共享记忆、成本感知消息改写、可靠性守卫和可观测接管。

当前比赛发布版本：`v0.5.15`

- 许可证：[Apache-2.0](LICENSE)
- Python：`>=3.11`
- 运行时包：`multi-agent-collaboration-runtime`
- 主要验证平台：openEuler

## 比赛验证快照

当前发布版本具有可校验的本地与 openEuler 不可变证据。

| 验证项目 | 已验证结果 |
|---|---:|
| openEuler 最终发行检查 | 22 / 22 通过 |
| 继承发行检查 | 15 / 15 通过 |
| Python 完整单元测试 | 602 / 602 通过 |
| wheel 内容检查 | 通过 |
| sdist 隔离安装、导入与 CLI 检查 | 通过 |
| 公开发布阻塞项 | 0 |

真实模型重复 A/B 实验中，两次完整独立重复的端到端通信 Token 合并结果如下：

| 指标 | 原生 AutoGen | AgentLite 托管 | 变化 |
|---|---:|---:|---:|
| 端到端通信 Token | 530,902 | 356,215 | **减少 32.90%** |
| 单次重复降幅范围 | - | - | 31.89% 至 33.92% |

该结果来自 120 次任务执行，只表示运行时协作消息与 Prompt View 的通信口径，
不等同于 Provider 计费 Token。另有冻结确定性机制实验测得 49.54% 至 85.51%
的局部通信压缩率，用于验证接管和改写路径，不作为当前真实模型 A/B 的总体
Token 降幅。精确口径、实验边界与复现入口见
[比赛结果快照](docs/competition/RESULTS_SNAPSHOT.md)。

## 双任务问题设计

比赛实验设置两个十阶段连续任务。任务组 A 验证偏好、预算、天气和方案的
连续修订；任务组 B 验证多跳证据、来源、污染纠偏和结论追溯。两题使用同一套
通用运行时，不在生产代码中加入领域词表、固定角色或题目专用逻辑。

- [双任务对照实验设计](docs/competition/AB_EXPERIMENT_DESIGN.md)
- [任务组 A：连续约束下的个性化旅行规划](docs/problems/A.md)
- [任务组 B：连续证据修订下的合成安全审计](docs/problems/B.md)

展示版文档说明问题价值、难点和评价方法；逐轮提示、状态示例、预注册文件和
执行脚本保留在 `experiments/`，用于完整复现实验。

## 核心能力

- **结构化状态传递**：在成本门和契约守卫允许时，使用 StatePool 引用和
  接收方专属 Prompt View 替代重复的完整文本传输。
- **受治理记忆**：覆盖类型化候选、准入、来源追踪、冲突消解、修订链路
  和任务级 MemoryView。
- **AutoGen 接管**：通过托管启动器接入 Team、Agent、Core、Studio Run
  绑定和最终交付边界。
- **语义保真**：采用开放式规范化事实、精确来源跨度、Schema Registry
  校验、类型化评审事件和受控语义消歧。
- **可靠性与可观测性**：提供安全回退、重试核算、不可变实验绑定、CLI
  报告和本地工作流监控。
- **发行工程**：提供 Apache-2.0 元数据、wheel/sdist 检查、隔离安装、
  openEuler 回归和 SHA256 证据归档。

## 安装与启动

以下命令均在仓库根目录执行。首次体验建议依次完成“创建环境、安装、自检、
无 Provider 演示”四步，再接入真实模型。

### 1. 获取源码并创建隔离环境

openEuler / Linux：

```bash
git clone https://github.com/stander1/agent.git
cd agent
git switch competition-submission
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
git clone https://github.com/stander1/agent.git
Set-Location agent
git switch competition-submission
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止当前终端激活虚拟环境，可先执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 2. 安装 AgentLite 与 AutoGen 适配依赖

从源码检出安装比赛版本：

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[autogen]"
```

如已取得比赛交付 wheel，也可以直接安装制品：

```bash
python -m pip install multi_agent_collaboration_runtime-0.5.15-py3-none-any.whl
```

源码安装适合阅读代码和运行仓库示例；wheel 安装适合验证最终交付制品。
两种方式任选一种，不要在同一个虚拟环境中重复混装不同版本。

### 3. 检查安装状态

```bash
agentlite version
agentlite doctor --framework autogen
```

`doctor` 会检查 Python 版本、`tiktoken`、AgentLite 启动器以及
`autogen-agentchat`、`autogen-core` 和 AutoGen 驱动。全部项目显示 `ok`
后再继续。需要机器可读结果时使用：

```bash
agentlite doctor --framework autogen --json
```

### 4. 运行无需 API Key 的快速演示

该演示使用相同的确定性 AutoGen Team 程序，对比原生运行与 AgentLite 托管
运行，不访问模型 Provider：

```bash
python examples/run_autogen_team_benchmark.py
```

命令结束后会打印 JSON 和 Markdown 报告路径，默认位于带时间戳的
`runs/v5.13h-autogen-team-benchmark-*` 目录。报告包含原生组、托管组、
通信 Token、接管路径、交付得分和安全回退计数。退出码为 `0` 且报告中的
`passed` 为 `true`，表示快速演示通过。

### 5. 配置真实模型接口

真实 AutoGen 应用使用 OpenAI-compatible 接口。非敏感配置可直接写入当前
终端环境；API Key 应通过不回显输入或系统密钥管理器注入，不能写入脚本、
命令历史、日志或仓库。

openEuler / Linux：

```bash
export OPENAI_BASE_URL="https://你的接口地址/v1"
export OPENAI_MODEL="你的模型名称"
read -rsp "OPENAI_API_KEY: " OPENAI_API_KEY
echo
export OPENAI_API_KEY
```

Windows PowerShell：

```powershell
$env:OPENAI_BASE_URL = "https://你的接口地址/v1"
$env:OPENAI_MODEL = "你的模型名称"
$secureKey = Read-Host "OPENAI_API_KEY" -AsSecureString
$credential = [pscredential]::new("agentlite", $secureKey)
$env:OPENAI_API_KEY = $credential.GetNetworkCredential().Password
Remove-Variable secureKey, credential
```

### 6. 启动 AgentLite 托管示例

每次正式运行都使用新的实验编号。`--data-dir` 保存 AgentLite 会话，
`--experiment-dir` 保存不可变实验绑定，目标程序的 `--output-dir` 应与
`--experiment-dir` 相同。

openEuler / Linux：

```bash
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="runs/quickstart-${RUN_ID}"
DATA_DIR=".agentlite-exp/quickstart-${RUN_ID}"

agentlite autogen \
  --data-dir "$DATA_DIR" \
  --experiment-dir "$RUN_DIR" \
  -- python examples/developer_autogen_code_app.py \
    --agent-config examples/developer_agent_config.json \
    --output-dir "$RUN_DIR" \
    --question "请设计一个低开销多智能体协作方案，并说明验证指标。"
```

Windows PowerShell：

```powershell
$RunId = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = "runs/quickstart-$RunId"
$DataDir = ".agentlite-exp/quickstart-$RunId"

agentlite autogen `
  --data-dir $DataDir `
  --experiment-dir $RunDir `
  -- python examples/developer_autogen_code_app.py `
    --agent-config examples/developer_agent_config.json `
    --output-dir $RunDir `
    --question "请设计一个低开销多智能体协作方案，并说明验证指标。"
```

`agentlite autogen` 默认启用受支持的全部 AutoGen 接管能力。成功启动时终端
应出现 `Bootstrap status: active`、`Driver hooks: active` 和
`Experiment binding verified: True`。核心产物位于本次 `RUN_DIR`：

| 产物 | 内容 |
|---|---|
| `final_answer.md` | 多 Agent 团队生成的最终答案 |
| `run_result.json` | 问题、完整消息、耗时和产物路径 |
| `llm_usage_summary.json` | Provider 返回的调用与 Token 汇总 |
| `agentlite_session_report.md` | AgentLite 接管、通信和记忆统计 |
| `agentlite_session_binding.json` | 实验目录与精确会话的绑定记录 |

实验目录采用不可变保护。若重复使用已有文件的 `RUN_DIR`，启动器会拒绝覆盖；
此时应生成新的 `RUN_ID`，不要删除或复用旧证据。

### 7. 托管已有 AutoGen 程序

普通 AutoGen Python 程序不需要显式导入 AgentLite，只需把原启动命令放在
`agentlite autogen --` 之后：

```bash
agentlite autogen -- python your_autogen_app.py --your-app-argument value
```

其中 `--` 用于分隔 AgentLite 参数和目标程序参数。常用接管档位如下：

| 档位 | 用途 |
|---|---|
| `--rewrite all` | 默认正式接管，启用受支持的改写与接收端还原 |
| `--rewrite off --broadcast-mode shadow-only` | 只观察和计量，不改变消息 |
| `--rewrite team` | 只启用 Team 任务入口相关改写 |
| `--rewrite non-text` | 启用 Handoff、ToolSummary 等非普通文本边界 |

例如，以只观察模式启动现有程序：

```bash
agentlite autogen \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python your_autogen_app.py
```

### 8. 查看报告与网页监控

从不可变实验目录重新输出会话报告：

```bash
agentlite report autogen-session \
  --experiment-dir "$RUN_DIR" \
  --format markdown
```

Windows PowerShell：

```powershell
agentlite report autogen-session `
  --experiment-dir $RunDir `
  --format markdown
```

在另一个终端启动本地工作流监控：

```bash
agentlite monitor \
  --runs-dir runs \
  --data-dir "$DATA_DIR"
```

Windows PowerShell：

```powershell
agentlite monitor `
  --runs-dir runs `
  --data-dir $DataDir
```

浏览器打开 `http://127.0.0.1:8765`，即可查看运行列表、Agent 交接、
StatePool、MemoryStore、Token 与错误状态。监控命令会持续占用当前终端，
按 `Ctrl+C` 停止；端口冲突时可增加 `--port 8766`。

真实模型运行结束后，可从当前终端移除凭据变量：

```bash
unset OPENAI_API_KEY
```

Windows PowerShell：

```powershell
Remove-Item Env:OPENAI_API_KEY
```

### 9. 常见启动问题

| 现象 | 检查方式 |
|---|---|
| `agentlite` 命令不存在 | 确认虚拟环境已激活，并使用同一环境执行安装与启动 |
| `doctor` 中 AutoGen 导入失败 | 重新执行 `python -m pip install -e ".[autogen]"` |
| `Driver hooks: inactive` | 确认目标是 `--` 后启动的 Python 进程，并检查是否绕过了正常 Python 启动流程 |
| 实验目录已存在或绑定失败 | 为本次运行生成新的 `RUN_ID`，不要覆盖历史目录 |
| 模型返回 401 / 403 | 检查接口地址、模型名和凭据权限，不要在终端打印 API Key |
| 监控页面无法打开 | 检查启动终端是否仍在运行，或更换 `--port` |

## 验证

在 openEuler 上运行最终无 Provider 发行门禁：

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

只有全部验收检查通过、wheel 与 sdist 均在源码目录外完成隔离安装、
公开发布阻塞项为空且制品哈希与记录一致时，才判定发行有效。

## 文档

- [比赛交付说明](docs/competition/v0.5.15-delivery-guide.md)
- [双任务对照实验设计](docs/competition/AB_EXPERIMENT_DESIGN.md)
- [任务组 A](docs/problems/A.md)与[任务组 B](docs/problems/B.md)
- [已验证结果快照](docs/competition/RESULTS_SNAPSHOT.md)
- [开发历程](docs/competition/DEVELOPMENT_RECORD.md)
- [最终发行说明](docs/release/v0.5.15-final-release-notes.md)
- [版本与分支映射](docs/versioning.md)
- [实验复现索引](docs/experiments/README.md)

历史实验和中间结果完整保留在 Git 提交历史中，用于追溯开发过程；当前
比赛分支只保留中文索引和最终复现入口。当前发布结论以比赛结果快照和最终
不可变验收证据为准。

## 凭据安全

Provider 凭据只能在运行时传入，不得提交到仓库、显示在命令行、写入日志
或进入实验归档。发行兼容性检查不需要 Provider 凭据。
