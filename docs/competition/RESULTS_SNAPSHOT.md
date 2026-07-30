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

## AutoGen 通信受控对照实验

冻结的 `v5.12x` 实验使用同一套确定性 AutoGen Team 程序，分别运行原生
模式和 AgentLite 托管模式。用户程序只导入 AutoGen，不导入 AgentLite。

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

## 指标边界

通信实验测量运行时边界上采用 tokenizer 统计的消息与 Prompt View 内容，
不将其等同于 Provider 计费 Token。

确定性 `12 / 12` 得分只验证该实验声明的交付契约，不表示所有模型、提示、
框架或自然语言领域都具有完全相同的质量。

所有当前发行结论均受已提交实验定义和取回的不可变证据约束。
