# v5.14e 当前任务保真与最终成果验收

本阶段复用 v5.14b 冻结的 A/B 任务、三组实验、Agent 配置、Provider、
温度、最大轮次、质量评分和 15% 成本阈值，不修改题目或放宽标准。

新增验收内容：

1. 当前用户任务中的结构化事实必须完整进入每个接收者视图；
2. 历史上下文可以按能力画像裁剪，当前任务不能为了减少 Token 而丢字段；
3. Reviewer 只做验收时，最终正文必须提升自前序完整成果；
4. 记忆查询无命中时允许不注入，有命中时必须发生真实注入；
5. 当前任务保真结果必须同时出现在 trace 和会话报告中。

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

export AGENTLITE_V514E_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.14e-current-task-fidelity-acceptance/run_openeuler.sh
bash experiments/v5.14e-current-task-fidelity-acceptance/run_openeuler.sh
```

输出：

```text
runs/v5.14e-current-task-fidelity-acceptance/<实验编号>/preflight_report.json
runs/v5.14e-current-task-fidelity-acceptance/<实验编号>/acceptance_report.json
exports/v5.14e-current-task-fidelity-acceptance-<实验编号>.tar.gz
exports/v5.14e-current-task-fidelity-acceptance-<实验编号>.tar.gz.sha256
```

即使验收失败，完整证据仍会先被打包，再以非零状态退出。
