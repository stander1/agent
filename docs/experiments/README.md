# 历史实验说明

AgentLite 的迭代实验报告完整保留在 Git 提交历史中，用于追溯实现决策、
中间测量结果以及推动后续修改的证据。当前比赛分支只保留本中文索引，避免
把已经被后续版本取代的阶段性结论与最终发行结论并列展示。

## 状态含义

- 文件名中的 `results` 表示一次有边界的运行结果，不代表永久产品属性。
- `formal-regression` 表示冻结的回归边界。
- 当后续版本修改相关实现或验收契约时，原中间结果由后续证据取代。
- 当前发行状态只由最终比赛结果快照和不可变发行验收归档确定。

## 当前证据入口

- `../competition/RESULTS_SNAPSHOT.md`
- `../competition/v0.5.15-delivery-guide.md`
- `../release/v0.5.15-final-release-notes.md`

可通过 GitHub 提交历史或以下命令查看完整实验记录：

```bash
git log --all -- docs/experiments experiments
```

历史记录保留任务范围、指标定义和证据边界。通信 Token、Provider Token、
确定性质量检查和模型评审交付质量属于不同指标，不能互相替代。
