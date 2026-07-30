# 历史文档

曾经作为仓库入口的文档快照完整保留在 Git 提交历史中。当前比赛分支不再
重复保存这些已经被取代的英文快照。

历史文件用于维持完整的开发记录，其中可能描述后续已被取代的阶段能力、
测量结果或适用边界。当前比赛结论以以下文档为准：

- 仓库根目录 `README.md`；
- `docs/competition/RESULTS_SNAPSHOT.md`；
- `docs/competition/v0.5.15-delivery-guide.md`；
- `docs/release/v0.5.15-final-release-notes.md`。

可通过 `git log --all -- docs/history README.md` 追溯历史快照。历史快照
不会进入 wheel、sdist 或比赛精选源码包。
