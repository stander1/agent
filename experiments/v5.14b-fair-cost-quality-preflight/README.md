# v5.14b 公平成本-质量预检

## 目标

本阶段不修改生产运行时。它在真实 openEuler、AutoGen 和 Provider 上，用两个不同领域的
三轮连续任务验证：

1. Native、Observed、Managed 是否使用相同任务、Agent、模型参数和最大轮次；
2. 不可变运行归档、AgentLite session 和 Provider usage 是否一一绑定；
3. Managed 是否真实改写消息、检索并注入同一协作组记忆；
4. Provider 实际 Token 与端到端协作 Token 是否同时下降；
5. 最终交付质量是否不低于 Native；
6. Managed 是否实际产生可审计的结构化非文本状态；
7. 证据链完整后，是否值得进入 A1-A10/B1-B10 正式实验。

## 两个预检场景

- `A1-A3`：旅行需求、三个候选地、初版行程；
- `B1-B3`：合成告警、网络观测、合成 payload 映射。

A 使用普通开发者实验中的旅行专家 Team；B 使用独立的合成安全审计专家 Team。Agent
角色配置只存在于实验目录，不进入 `agent_runtime`。B 组资料快照直接写入每轮用户任务，
三组看到完全相同的资料，不依赖不存在的外部文件或工具。

## 三组口径

| 组别 | AgentLite | 消息改写 | 共享记忆 |
|---|---|---|---|
| Native | 不启动 | 无 | 无 |
| Observed | 启动 | `rewrite off` | 不驱动任务 |
| Managed | 启动 | `rewrite all` | 同一场景独立作用域 |

Observed 用于观察“仅启动钩子”带来的自然生成波动，不能作为优化组。

## 运行前冻结的阈值

阈值保存在 `preregistration.json`，必须在 Provider 调用前进入 Git：

```text
Managed Provider 总 Token 降低 >= 15%
Managed 端到端协作 Token 降低 >= 15%
Managed 匿名综合质量 >= Native - 0.3/10
Managed 完整交付数 >= Native
rewrite_error_fallback_count = 0
```

匿名综合质量取：

```text
min(综合任务裁判分, 技术审查裁判分)
```

评审模型的 Token 单独记录，不计入运行时协作成本。

## openEuler 运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-agentlite/bin/activate

export OPENAI_API_KEY='你的密钥'
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'
export OPENAI_TIMEOUT_SECONDS='300'
export OPENAI_MAX_RETRIES='6'
export OPENAI_RETRY_BACKOFF_SECONDS='3'

chmod +x experiments/v5.14b-fair-cost-quality-preflight/run_openeuler.sh
bash experiments/v5.14b-fair-cost-quality-preflight/run_openeuler.sh
```

脚本拒绝覆盖已有目录，也拒绝在存在已跟踪未提交修改时开始实验。即使预检未通过，也会
打包完整证据。

## 结果解释

- `evidence_pipeline_passed=true`：证据结构完整，数据可用于诊断；
- `preliminary_targets_met=true`：成本、质量和可靠性初步达标；
- `ready_for_formal_run=true`：才允许扩展为 A1-A10/B1-B10；
- 任一目标失败：先分析具体成本分量、质量缺口或 fallback，不直接扩大实验。

报告中的 `state_summary` 来自真实 StatePool 快照，不从提示词推断状态类型。预检只要求
结构化 `artifact_state` 确实出现；`retrieval_state` 与 `embedding_state` 未真实产生时
保持为 0，并留给带真实检索/向量工具的正式实验验证。

本预检不用于最终比赛统计，也不允许在看到结果后修改阈值并重新解释同一证据。
