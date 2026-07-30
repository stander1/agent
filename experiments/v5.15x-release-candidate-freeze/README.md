# v5.15x 发行候选冻结

该门禁在 openEuler 上构建并验证 AgentLite `0.5.15rc1`，将独立安装包门禁、
完整源码发行门禁以及 wheel/sdist 制品构建器统一到一个不可变证据归档中。

在干净且已跟踪的检出目录中运行：

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515X_RUN_ROOT
unset AGENTLITE_V515X_EXPORT_BASE
export AGENTLITE_V515X_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15x-release-candidate-freeze/run_openeuler.sh
```

该实验不需要 Provider 凭据。在项目所有者选定明确的仓库许可证之前，即使
技术 RC 已通过，也仍会标记为尚未达到公开开源发布条件。
