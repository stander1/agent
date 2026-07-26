# v5.14h 正式规模回归验收

本阶段使用与 v5.14f 完全相同的 A1-A10、B1-B10 冻结任务、Agent 配置、MiMo
模型、温度、最大轮次和原有成本质量阈值，验证 v5.14g 通用修复是否消除正式实验中
的错误记忆命中和审查冲突传播。

新增门禁只覆盖动态审查治理证据：

- 每个场景必须观察到由能力画像或语义动作识别出的审查治理事件；
- 阻断事件定向命中的 active 记忆必须全部软废弃；
- 不得软废弃未被审查证据定位的其他记忆；
- 阻断结论必须经过候选池和规则准入进入共享记忆；
- 非阻断审查不得产生记忆生命周期副作用。

阻断事件数量不是强制门禁。若本次 Provider 直接生成合格结果，可以没有阻断；
但一旦出现阻断，上述治理链路必须全部成立。

## openEuler 运行

```bash
cd /home/competition/multi-agent-runtime

git status --short
git pull --ff-only origin main

source .venv-agentlite/bin/activate
python -m pip install -e ".[autogen]"

read -rsp "MiMo API Key: " OPENAI_API_KEY
echo
export OPENAI_API_KEY
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'
export OPENAI_TIMEOUT_SECONDS='300'
export OPENAI_MAX_RETRIES='6'
export OPENAI_RETRY_BACKOFF_SECONDS='3'

unset AGENTLITE_V514H_RUN_ROOT
unset AGENTLITE_V514H_TRACE_ROOT
unset AGENTLITE_V514H_EXPORT_BASE
unset AGENTLITE_V514H_RESUME
export AGENTLITE_V514H_EXP_ID="$(date +%Y%m%d-%H%M%S)"

experiments/v5.14h-formal-regression-acceptance/run_openeuler.sh
```

若因关机或网络中断需要续跑，保留原实验编号并执行：

```bash
export AGENTLITE_V514H_RESUME=1
experiments/v5.14h-formal-regression-acceptance/run_openeuler.sh
```

续跑会跳过已经完整的运行组及其比较、报告和双重质量评分；不完整的运行组或
后处理目录会先移动到 `runs/.../interrupted/`，随后重建，不会覆盖旧证据。

完成后会生成：

```text
runs/v5.14h-formal-regression-acceptance/<EXP_ID>/formal_regression_report.json
runs/v5.14h-formal-regression-acceptance/<EXP_ID>/formal_regression_report.md
exports/v5.14h-formal-regression-acceptance-<EXP_ID>.tar.gz
exports/v5.14h-formal-regression-acceptance-<EXP_ID>.tar.gz.sha256
```
