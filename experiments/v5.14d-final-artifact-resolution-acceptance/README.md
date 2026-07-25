# v5.14d 最终成果与路由卫生验收

该阶段复用 v5.14b 已冻结的 A/B 题、三组设置、Provider、温度、最大轮次、双盲评分和
15% 成本阈值，不重新定义成功标准。

新增门禁只验证 v5.14d 修复是否真实进入实验链：

1. 每个有效交付都有 `final_resolution_kind` 和成果来源；
2. Reviewer 的批准说明不能被当成最终正文；
3. 明确数值上限不能与最终结果矛盾；
4. AutoGen 路由元数据不能进入 StatePool；
5. AgentLite 协议标记不能出现在模型正文；
6. `state_reused` 必须进入报告，状态复用次数不能继续误显示为 0。

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

export AGENTLITE_V514D_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.14d-final-artifact-resolution-acceptance/run_openeuler.sh
bash experiments/v5.14d-final-artifact-resolution-acceptance/run_openeuler.sh
```

输出包括：

```text
runs/v5.14d-final-artifact-resolution-acceptance/<实验编号>/preflight_report.json
runs/v5.14d-final-artifact-resolution-acceptance/<实验编号>/acceptance_report.json
exports/v5.14d-final-artifact-resolution-acceptance-<实验编号>.tar.gz
exports/v5.14d-final-artifact-resolution-acceptance-<实验编号>.tar.gz.sha256
```

即使基础预检或 v5.14d 附加门禁失败，脚本仍会先打包完整证据，再以非零状态退出。
