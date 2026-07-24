# v5.13z 错误记忆采用修复闭环验收

本阶段验证的是通用运行时机制，不使用 Question A、旅游领域词表或固定
Planner/Writer/Reviewer 名称。

验收覆盖：

- 结构化旧值可证明安全时，在消息进入下游前精确替换为当前有效值；
- 同一输出混用当前值和历史值时归一化为当前值；
- 相同数字出现在无关位置时不误改；
- 无法证明安全替换时，用 `conflict_alert` 替代原始输出；
- 原始错误采用仍记入审计，修复后的输出正常写入 `artifact_state`；
- 修复、阻断和执行失败作为独立指标进入会话报告。

本机运行：

```powershell
F:\software\anaconda\envs\agentlite-autogen\python.exe `
  experiments\v5.13z-memory-adoption-repair\verify_repair_guard.py `
  --output-dir runs\v5.13z-memory-adoption-repair\local
```

openEuler 运行：

```bash
cd /home/competition/multi-agent-runtime
source .venv-oe-v513m/bin/activate
python -m pip install -e ".[autogen]"

export AGENTLITE_V513Z_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.13z-memory-adoption-repair/run_openeuler.sh
bash experiments/v5.13z-memory-adoption-repair/run_openeuler.sh
```

该验收不调用 LLM，不需要 API Key。
