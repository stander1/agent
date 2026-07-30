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
