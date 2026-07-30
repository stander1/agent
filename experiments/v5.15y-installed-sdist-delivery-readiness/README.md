# v5.15y 最终已安装 sdist 与交付就绪

该门禁在 openEuler 上构建 AgentLite `0.5.15`，并在继承的制品检查基础上，
增加最终版本一致性、隔离 sdist 安装、导入来源、已安装 CLI、软件包发现
元数据和当前比赛交付指南检查。

在干净且已跟踪的检出目录中运行：

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515Y_RUN_ROOT
unset AGENTLITE_V515Y_EXPORT_BASE
export AGENTLITE_V515Y_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

该实验不需要 Provider 凭据。最终报告会校验仓库、软件包元数据、wheel 和
sdist 中的 Apache-2.0，要求版本不是预发行版本，并要求清除所有发布阻塞项。
