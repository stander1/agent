# AgentLite v0.5.15 已验证结果快照

## 发行就绪状态

`v0.5.15` 最终 openEuler 发行证据记录如下：

| 检查组 | 结果 |
|---|---:|
| 最终 sdist 隔离安装验收 | 22 / 22 通过 |
| 继承发行验收 | 15 / 15 通过 |
| Python 完整单元测试 | 602 / 602 通过 |
| 最终发行定向测试 | 20 / 20 通过 |
| wheel 检查 | 通过 |
| sdist 检查 | 通过 |
| sdist 隔离安装与导入 | 通过 |
| 已安装 CLI 版本身份 | 通过 |
| Apache-2.0 源码与包身份 | 通过 |
| 公开发布阻塞项 | 0 |

验收报告记录项目版本与运行时版本均为 `0.5.15`，并满足
`technical_release_ready=true`、`open_source_publication_ready=true`
和 `installed_sdist_verified=true`。

复现入口：

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

## 真实模型重复 A/B 通信结果

正式重复 A/B 实验采用相同模型、温度、任务、角色与评审配置，对比原生
AutoGen 和 AgentLite 托管模式。两次完整独立重复共包含 120 次任务执行，
端到端通信统计如下：

| 指标 | 原生模式 | 托管模式 | 托管模式变化 |
|---|---:|---:|---:|
| 端到端通信 Token | 530,902 | 356,215 | **减少 32.90%** |
| 第一次完整重复 | - | - | 减少 33.92% |
| 第二次完整重复 | - | - | 减少 31.89% |

这里的“端到端通信 Token”统计运行时协作消息和 Prompt View，不等同于
Provider 计费 Token。该批次原计划三个重复，其中两个形成完整归档，因此
当前只在两次完整重复及其合并统计边界内陈述结果，不将它写成全部门禁通过。

### 真实模型质量门禁状态

上述通信统计不能替代质量门禁。取回的两次完整重复显示：Managed 的 Provider
Token、质量和完整交付没有同时达到预注册要求；任务组 A 的托管组质量与交付低于
Native，任务组 B 的托管组 Provider 用量增加且质量与交付下降。记忆命中中仍有
大量记录未完成有效采用评估，第三次重复也没有形成完整 B 组证据。因此当前仓库不
声称真实模型下质量非劣或 Provider 成本下降。后续通用修复与验证记录见
`QUALITY_REPAIR_STATUS.md`。

## AutoGen 确定性机制对照实验

冻结的 `v5.12x` 实验使用同一套确定性 AutoGen Team 程序，分别运行原生
模式和 AgentLite 托管模式。用户程序只导入 AutoGen，不导入 AgentLite。
该实验用于隔离验证透明接管和局部消息改写机制，不代表真实模型 A/B 的总体
Token 降幅。

| 指标 | 原生模式 | 托管模式 | 托管模式变化 |
|---|---:|---:|---:|
| 首条 Team 输入 Token | 2,071 | 1,045 | -49.54% |
| Team 广播传输 Token | 6,213 | 900 | -85.51% |
| Agent 输入 Token | 3,315 | 919 | -72.28% |
| 确定性交付得分 | 12 / 12 | 12 / 12 | 0 |

该实验 20 / 20 项检查全部通过。托管组在实验正常路径中记录 1 次 Team
改写和 3 次 Agent 输入改写，安全回退次数为 0。

详细证据：

- `docs/experiments/v5.12x-autogen-team-benchmark-results.md`
- `examples/run_autogen_team_benchmark.py`

## 双任务实验入口

比赛问题设计采用两个互补的十阶段连续任务：

- 任务组 A：连续约束下的个性化旅行规划；
- 任务组 B：连续证据修订下的合成安全审计。

统一对照组、控制变量、成本质量指标和盲评方法见
`docs/competition/AB_EXPERIMENT_DESIGN.md`。展示版问题位于
`docs/problems/A.md` 和 `docs/problems/B.md`，逐轮执行规约及脚本位于
`experiments/`。问题设计与已验证结果分开陈述，不以实验计划替代结果证据。

## 指标边界

两类通信实验均测量运行时边界上采用 tokenizer 统计的消息与 Prompt View
内容，不将其等同于 Provider 计费 Token。README 的 `32.90%` 是两次完整
真实模型重复的合并通信结果；`49.54%` 至 `85.51%` 是确定性机制实验中不同
局部边界的压缩率，两者不能混写为同一个指标。

确定性 `12 / 12` 得分只验证该实验声明的交付契约，不表示所有模型、提示、
框架或自然语言领域都具有完全相同的质量。

所有当前发行结论均受已提交实验定义和取回的不可变证据约束。
