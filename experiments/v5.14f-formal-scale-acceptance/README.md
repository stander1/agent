# v5.14f 正式规模验收

本阶段在 v5.14e 已通过的运行时机制上执行一次完整的
`A1-A10/B1-B10` 正式规模实验。它不新增生产运行时规则，也不以缩短输出、
减少轮次或修改 Agent 角色制造成本优势。

## 冻结口径

- 三组均执行相同的 20 个任务、Agent 配置、模型、`temperature=0` 和
  `max_turns=9`；
- A 组的出发地、人数、候选地和费用来自冻结的本地合成快照，避免模型自由补全；
- B 组只使用冻结的防御性合成证据；
- Provider Token、端到端协作 Token 和双重匿名质量继续使用 v5.14b 的口径；
- 阈值在 Provider 调用前写入 `preregistration.json`，不得看结果后修改；
- A10/B10 必须完整交付，且终局质量不得显著低于 Native；
- 记忆必须有真实命中、注入和有效命中，错误记忆命中必须为 0；
- 当前任务保真、协议隔离、路由状态净化和改写 fallback 继续作为硬门禁。

状态类型只读取真实 StatePool。B 组提示中出现 `retrieval_state` 或
`embedding_state` 不代表运行时已经产生该类型；报告会把缺失类型列为诊断项，
但本阶段不把它伪装成已实现。

## 运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-agentlite/bin/activate

read -rsp "MiMo API Key: " OPENAI_API_KEY
echo
export OPENAI_API_KEY
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'
export OPENAI_TIMEOUT_SECONDS='300'
export OPENAI_MAX_RETRIES='6'
export OPENAI_RETRY_BACKOFF_SECONDS='3'

export AGENTLITE_V514F_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.14f-formal-scale-acceptance/run_openeuler.sh
bash experiments/v5.14f-formal-scale-acceptance/run_openeuler.sh
```

完整实验包含 60 次任务执行和 40 次匿名评审调用，耗时会明显高于三任务预检。
即使验收失败，runner 也会先打包完整证据再以非零状态退出。

## 输出

```text
runs/v5.14f-formal-scale-acceptance/<实验编号>/preflight_report.json
runs/v5.14f-formal-scale-acceptance/<实验编号>/formal_acceptance_report.json
exports/v5.14f-formal-scale-acceptance-<实验编号>.tar.gz
exports/v5.14f-formal-scale-acceptance-<实验编号>.tar.gz.sha256
```
