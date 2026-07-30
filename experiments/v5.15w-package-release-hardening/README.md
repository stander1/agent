# v5.15w 软件包发行加固

该门禁证明当前 AgentLite 实现能够在 openEuler 上构建并安装为 wheel，
同时证明已安装的 AutoGen 接管支持两种有效运行结果：

- 降低成本的内部改写；或
- 不改变消息的显式 `token_not_reduced` 回退。

门禁还会检查 v5.15 语义桥模块是否已进入 wheel、源码与已安装版本是否一致、
完整发行门禁是否通过，以及证据归档的 SHA256 是否有效。

在干净且已跟踪的检出目录中运行：

```bash
cd /home/competition/multi-agent-runtime
unset AGENTLITE_V515W_RUN_ROOT
unset AGENTLITE_V515W_EXPORT_BASE
export AGENTLITE_V515W_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15w-package-release-hardening/run_openeuler.sh
```

该实验不需要 Provider 凭据。
