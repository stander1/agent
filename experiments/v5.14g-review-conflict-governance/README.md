# v5.14g 动态审查冲突治理验收

本阶段修复 v5.14f 正式规模实验暴露的两个通用问题：

1. `预算：两人总计 3000 元` 被错误解析为历史预算 `2`，造成错误记忆命中误报；
2. 具备审查能力的 Agent 明确判定旧结论违反硬约束后，该结论仍保持 active，
   后续任务继续复用。

实现不识别 Question A/B，不绑定旅行领域，也不要求 Agent 名称为 Reviewer。
审查权限来自动态能力画像或语义动作；只软废弃审查文本中可定位的 active
事实，并把阻断结论作为结构化候选记忆走统一准入路径。

## openEuler 运行

```bash
cd /home/competition/multi-agent-runtime
source .venv-agentlite/bin/activate
git pull --ff-only origin main
python -m pip install -e ".[autogen]"

unset AGENTLITE_V514G_RUN_ROOT
unset AGENTLITE_V514G_EXPORT_BASE
export AGENTLITE_V514G_EXP_ID="$(date +%Y%m%d-%H%M%S)"

chmod +x experiments/v5.14g-review-conflict-governance/run_openeuler.sh
bash experiments/v5.14g-review-conflict-governance/run_openeuler.sh
```

本阶段不调用 LLM Provider，因此不需要 API Key。验收通过后再进入正式
A1-A10/B1-B10 Provider 复跑，不能用本阶段结果直接宣称真实质量已恢复。
