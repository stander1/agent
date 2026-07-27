# v5.14m Reviewer 成果所有权与结论连续性验收

本阶段根据 v5.14l 正式 Provider 回归的真实失败证据，验证三项通用修复：

1. 最终标题下的修订清单仍会被终稿守卫拒绝；
2. Reviewer 的长篇验收报告不会替代业务成果，而会晋升最近一份已验收成果；
3. `最终/当前/综合结论、判断、状态` 及英文等价标签会进入类型化决策槽位，
   但 `agent_result=value` 这类普通标识符不会被误识别。

该验收不调用 Provider，不认识 Question A/B，也不依赖固定的
Planner/Writer/Reviewer 三角色名称。用于下一轮正式实验的 A/B Agent 配置
单独冻结在本目录，旧 v5.14l 配置及其哈希保持不变。

在 openEuler 仓库根目录运行：

```bash
source .venv-agentlite/bin/activate
unset AGENTLITE_V514M_RUN_ROOT
unset AGENTLITE_V514M_EXPORT_BASE
export AGENTLITE_V514M_EXP_ID="$(date +%Y%m%d-%H%M%S)"
chmod +x experiments/v5.14m-reviewer-artifact-continuity/run_openeuler.sh
bash experiments/v5.14m-reviewer-artifact-continuity/run_openeuler.sh
```

成功后生成：

```text
runs/v5.14m-reviewer-artifact-continuity/<EXP_ID>/acceptance_report.json
runs/v5.14m-reviewer-artifact-continuity/<EXP_ID>/acceptance_report.md
exports/v5.14m-reviewer-artifact-continuity-<EXP_ID>.tar.gz
exports/v5.14m-reviewer-artifact-continuity-<EXP_ID>.tar.gz.sha256
```
