# v5.14a 真实 AutoGen 记忆事实故障注入验收

本阶段把 v5.13z 已通过的确定性守卫放入真实 AutoGen 消息链，并使用真实
OpenAI-compatible Provider 生成受控故障输出。

它验证的不是“离线函数能否修改字符串”，而是：

1. Provider 原始输出是否真的包含指定故障；
2. 原生 AutoGen 是否把故障传给下游 Agent；
3. AgentLite 是否在消息离开发出 Agent 前完成修复或阻断；
4. 下游 Agent 实际收到的内容是否已经安全；
5. 正确输出和明确否定旧值是否被误拦截；
6. 修复是否误改同值但不同语义键的字段。

## 公平口径

- `FaultEmitter` 是唯一调用真实 LLM 的 Agent。
- `DownstreamProbe` 是确定性观察 Agent，只记录 AutoGen 实际传播给它的消息，
  不调用 LLM，也不参与质量判断。
- Native 与 Managed 使用同一模型、温度、故障矩阵和重复次数。
- 如果 Provider 没有实际产生危险旧值，该行记为“故障未成功注入”，整体验收失败，
  不会把它冒充为 AgentLite 防护成功。否定旧值场景至少需要一条真实否定样本；其余只
  输出活动值且未触发守卫的重复样本按安全退化记录。
- 默认每个场景重复 2 次，共 6 个场景、2 个组、24 次真实 LLM 调用。
- 本实验输出很短，只用于传播安全验收；Token 会完整记录，但不据此宣称通信成本下降。

## openEuler 一键运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-agentlite/bin/activate
python -m pip install -e ".[autogen]"

export OPENAI_API_KEY='你的密钥'
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'
export OPENAI_TIMEOUT_SECONDS='300'
export OPENAI_MAX_RETRIES='6'
export OPENAI_RETRY_BACKOFF_SECONDS='3'

export AGENTLITE_V514A_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.14a-real-memory-fault-injection/run_openeuler.sh
bash experiments/v5.14a-real-memory-fault-injection/run_openeuler.sh
```

可选地调整重复次数：

```bash
export AGENTLITE_V514A_REPETITIONS='3'
```

主要结果：

```text
runs/v5.14a-real-memory-fault-injection/<EXP_ID>/acceptance_report.md
runs/v5.14a-real-memory-fault-injection/<EXP_ID>/acceptance_report.json
runs/v5.14a-real-memory-fault-injection/<EXP_ID>/fault_audit.csv
exports/v5.14a-real-memory-fault-injection-<EXP_ID>.tar.gz
exports/v5.14a-real-memory-fault-injection-<EXP_ID>.tar.gz.sha256
```

`fault_audit.csv` 逐行保留故障是否成功注入、预期和实际守卫动作、Native 是否泄漏、
Managed 是否仍有故障逃逸，以及无关同值字段是否保留。
