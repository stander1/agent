# v5.13y 真实 AutoGen 事实级公平对照

## 目标

在 v5.13x 已通过确定性门禁后，使用真实 AutoGen 和真实 Provider 运行
Native、Observed、Managed 三组连续任务，验证事实级 TLC-Memory 是否在质量、
成本、连续性和可追溯性上成立。

本目录只是实验层。`agent_runtime` 不认识 D1-D3，也不固定 Planner、Writer、
Reviewer 等角色名。

## 公平口径

三组严格共用：

- 相同的任务序列和同一个 Team 配置；
- 相同的 Provider、模型、temperature、最大轮次和重试参数；
- 每组内部使用同一个 Team 实例连续完成 D1-D3；
- 每组使用独立输出目录，Managed 使用本次实验独立的记忆作用域；
- 质量评分先匿名冻结，再打开 Native、Observed、Managed 映射。

三类成本分别报告：

1. Provider 实际 Prompt、Completion、Total Token；
2. 相同 `task_id + agent + 调用序号` 的归一化公共调用 Token；
3. AgentLite 的消息、Prompt View、记忆读取、控制和重试端到端协作 Token。

质量由主盲评和独立技术盲评共同决定，最终分取两者较低值。评审模型的 Token
单独记录，不并入运行时协作成本。

## 事实级证据

报告新增：

- `raw_claim_count`：原始事实数量；
- `provisional_claim_count`：形成临时 ClaimCard 的数量；
- `slot_mapping_success_count`：成功映射规范 Slot 的数量；
- `memory_conflict_*`：冲突发现、解决和未解决数量；
- `active_memory_value_selection_count`：选出的当前有效事实值数量；
- `fact_attribution_sample.csv`：供人工标注假阳性、假阴性和不确定样本。

CSV 的人工标签为空时，只表示“已生成待复核样本”，不能表述为人工质量检查已通过。

## openEuler 运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-oe-v513m/bin/activate
git pull --ff-only origin main
python -m pip install -e ".[autogen]"

export OPENAI_API_KEY='你的密钥'
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'

chmod +x experiments/v5.13y-real-autogen-fact-memory/run_openeuler.sh
bash experiments/v5.13y-real-autogen-fact-memory/run_openeuler.sh
```

脚本拒绝覆盖既有证据。需要重跑时设置新的实验编号：

```bash
export AGENTLITE_V513Y_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.13y-real-autogen-fact-memory/run_openeuler.sh
```

即使最终验收存在失败项，脚本也会先打包完整证据，再以非零状态退出。
