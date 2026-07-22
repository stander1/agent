# v5.13s 动态能力画像真实 AutoGen 验收实验

## 1. 本轮要证明什么

这一步不再使用固定的 `planner / writer / reviewer` 角色名，而是在真实 AutoGen
运行中验证 AgentLite 能否：

1. 从任意 Agent 的名称、角色描述、系统提示词和工具元数据生成能力画像；
2. 识别 `EvidenceMiner` 绑定的真实 AutoGen `FunctionTool`；
3. 根据运行中观察到的语义动作、成功情况、Token 和时延更新画像；
4. 为不同接收者生成动态的最小充分上下文视图；
5. 在 D1 -> D2 -> D3 连续任务中注入必要共享记忆；
6. 真实改写 AutoGen 消息，同时保持最终交付质量不显著低于原生组。

## 2. 公平实验口径

三组均使用同一份任务、同一组 Agent、同一模型参数、同一最大轮次，并在一个
AutoGen Team 实例内连续执行 D1、D2、D3：

| 组别 | AgentLite | 消息改写 | 用途 |
|---|---|---|---|
| `native` | 不启动 | 无 | 原生 AutoGen 对照 |
| `observed` | 启动 | 关闭，仅影子测量 | 测量钩子本身的影响 |
| `managed` | 启动 | `--rewrite all` | 验证正式接管 |

四个业务角色分别为 `ScopeCartographer`、`EvidenceMiner`、
`DesignSynthesizer` 和 `IntegritySentinel`。这些名字不属于 AgentLite 内置角色。

`EvidenceMiner` 每轮实际调用本地 `FunctionTool`，因此验收的不只是提示词推断，
还包括工具能力发现和真实工具执行。

## 3. openEuler 运行前准备

```bash
cd /home/competition/multi-agent-runtime
git pull --ff-only origin main

source .venv-agentlite/bin/activate
python -m pip install -e ".[autogen]"

agentlite version
agentlite doctor --framework autogen --json
```

设置模型连接。Key 只放环境变量，不写入仓库或实验归档：

```bash
export OPENAI_API_KEY='你的 MiMo Key'
export OPENAI_BASE_URL='https://token-plan-cn.xiaomimimo.com/v1'
export OPENAI_MODEL='mimo-v2.5'
export OPENAI_TIMEOUT_SECONDS='300'
export OPENAI_MAX_RETRIES='6'
export OPENAI_RETRY_BACKOFF_SECONDS='3'
```

先做一次最小连通性检查。返回模型正常响应后再进行长实验：

```bash
curl --location --http1.1 -sS \
  --connect-timeout 15 --max-time 300 \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$OPENAI_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 OK\"}],\"temperature\":0,\"stream\":false}" \
  "$OPENAI_BASE_URL/chat/completions"
```

## 4. 一键运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-agentlite/bin/activate

chmod +x experiments/v5.13s-dynamic-capability-acceptance/run_openeuler.sh
bash experiments/v5.13s-dynamic-capability-acceptance/run_openeuler.sh
```

脚本依次完成三组运行、不可变归档绑定校验、三组匿名质量评分、动态能力画像验收和
数据打包。为了防止混入旧记忆，每次运行会建立新的目录，并设置唯一的
`AGENTLITE_MEMORY_SCOPE`。

如需人工指定实验编号，可在运行前设置：

```bash
export EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.13s-dynamic-capability-acceptance/run_openeuler.sh
```

脚本拒绝覆盖任何同名目录。网络中断后应使用新的 `EXP_ID` 重跑，保留失败归档用于
解释 Provider 波动，不要把两次运行的数据拼成一组。

若自动验收有检查项未通过，脚本仍会先生成报告和压缩包，再以非零状态退出。这样既不
把失败伪装成成功，也不会丢失定位问题所需的 Trace 和逐步输出。

## 5. 结果位置

设实验编号为 `<EXP_ID>`：

```text
runs/v5.13s-dynamic-capability/<EXP_ID>/
  native/
  observed/
  managed/
  comparison/
  acceptance_report.json
  acceptance_report.md

.agentlite-exp/v5.13s-dynamic-capability/<EXP_ID>/

exports/v5.13s-dynamic-capability-<EXP_ID>.tar.gz
exports/v5.13s-dynamic-capability-<EXP_ID>.tar.gz.sha256
```

每个任务的 `tasks/<task_id>/run_result.json` 保存完整团队消息、工具调用、最终输出、
真实 Provider Token 和时延，因此分析中可以回看每一步 LLM 输出。

## 6. 验收口径

自动验收的硬性条件包括：

- AgentLite 驱动和钩子激活；
- 四个任意名称 Agent 均形成非空能力画像；
- 不依赖固定 `planner / writer / reviewer` 业务角色；
- 真实识别并执行 `local_evidence_lookup` 工具；
- 运行反馈使画像版本更新；
- 动态能力上下文视图和真实消息改写均发生；
- 连续任务检测到依赖并注入必要记忆；
- 三组均完成三个任务；
- 接管组匿名质量均分不低于原生组 0.5 分以上，完整交付数不减少。

Provider 总 Token、AgentLite 通信 Token、记忆读取、控制和重试成本全部保留在报告中。
本实验不会以“每条消息必须变短”作为成功条件，也不会只用总 Token 降低来证明系统
正确；最终需要结合端到端成本和匿名质量解释结果。

## 7. 从虚拟机取回

在 Windows PowerShell 中执行，替换 `<EXP_ID>`：

```powershell
scp root@192.168.229.128:/home/competition/multi-agent-runtime/exports/v5.13s-dynamic-capability-<EXP_ID>.tar.gz "C:\Users\千梦\Desktop\"
scp root@192.168.229.128:/home/competition/multi-agent-runtime/exports/v5.13s-dynamic-capability-<EXP_ID>.tar.gz.sha256 "C:\Users\千梦\Desktop\"
```
