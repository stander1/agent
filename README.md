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

受控的确定性 AutoGen Team 对照实验验证了透明接管与通信量下降，并保持
该实验声明的交付契约：

| 指标 | 原生 AutoGen | AgentLite 托管 | 变化 |
|---|---:|---:|---:|
| 首条 Team 输入 Token | 2,071 | 1,045 | -49.54% |
| Team 广播传输 Token | 6,213 | 900 | -85.51% |
| Agent 输入 Token | 3,315 | 919 | -72.28% |
| 确定性交付得分 | 12 / 12 | 12 / 12 | 无损失 |

以上 Token 数据是冻结确定性实验中的通信层测量结果，不等同于 Provider
计费 Token，也不作为所有领域普遍质量一致的证明。精确口径与复现入口见
[比赛结果快照](docs/competition/RESULTS_SNAPSHOT.md)。

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

## 安装

从 wheel 安装：

```bash
python -m pip install multi_agent_collaboration_runtime-0.5.15-py3-none-any.whl
agentlite version
agentlite doctor --framework autogen --json
```

开发与 AutoGen 集成安装：

```bash
python -m pip install -e ".[autogen]"
```

使用 AgentLite 托管现有 AutoGen 程序：

```bash
agentlite autogen -- python your_autogen_app.py
```

## 验证

在 openEuler 上运行最终无 Provider 发行门禁：

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

只有全部验收检查通过、wheel 与 sdist 均在源码目录外完成隔离安装、
公开发布阻塞项为空且制品哈希与记录一致时，才判定发行有效。

## 文档

- [比赛交付说明](docs/competition/v0.5.15-delivery-guide.md)
- [已验证结果快照](docs/competition/RESULTS_SNAPSHOT.md)
- [开发历程](docs/competition/DEVELOPMENT_RECORD.md)
- [最终发行说明](docs/release/v0.5.15-final-release-notes.md)
- [版本与分支映射](docs/versioning.md)
- [历史实验说明](docs/experiments/README.md)

历史实验和中间结果完整保留在 Git 提交历史中，用于追溯开发过程；当前
比赛分支只保留中文索引和最终复现入口。当前发布结论以比赛结果快照和最终
不可变验收证据为准。

## 凭据安全

Provider 凭据只能在运行时传入，不得提交到仓库、显示在命令行、写入日志
或进入实验归档。发行兼容性检查不需要 Provider 凭据。
