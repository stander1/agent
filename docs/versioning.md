# 版本控制策略

本项目采用“滚动主干 + 版本快照”的方式管理迭代。

GitHub 默认打开的是 `main` 分支的最新状态，所以 v0、v1 的代码和文档会在最新目录中同时出现。这不是最终版本边界，而是当前开发主干的状态。真正的版本边界由 Git tag 和 `version/*` 分支固定。

## 分支与标签约定

| 类型 | 用途 | 是否继续开发 |
| --- | --- | --- |
| `main` | 当前最新开发主干，持续向 v2/v3 推进 | 是 |
| `version/v0-baseline` | v0 基线评测版本快照，便于在 GitHub 直接浏览 | 否 |
| `version/v1-runtime-lite` | v1 三线 Lite 闭环版本快照，便于在 GitHub 直接浏览 | 否 |
| `v0.0-baseline` | v0 不可变标签 | 否 |
| `v1.0-runtime-lite` | v1 不可变标签 | 否 |

## 当前版本边界

| 版本 | Git 引用 | 说明 |
| --- | --- | --- |
| v0 | `v0.0-baseline` / `version/v0-baseline` | 纯文本基线、tokenizer 统计、metrics/trace 输出、任务集与测试 |
| v1 | `v1.0-runtime-lite` / `version/v1-runtime-lite` | SHP-lite、StatePool-lite、MemoryStore-lite、端到端成本统计、v0/v1 实验记录 |

## 如何在本地切换版本

查看 v0：

```powershell
git fetch --all --tags
git switch version/v0-baseline
```

查看 v1：

```powershell
git fetch --all --tags
git switch version/v1-runtime-lite
```

回到最新开发主干：

```powershell
git switch main
```

也可以直接按 tag 查看不可变快照：

```powershell
git checkout v0.0-baseline
git checkout v1.0-runtime-lite
```

注意：直接 checkout tag 会进入 detached HEAD 状态，只适合查看或复现实验，不适合继续开发。

## 后续迭代规则

- 每完成一个可运行版本，就创建一个 tag，例如 `v2.0-contract-guard`。
- 每个比赛展示用版本，同时创建一个 `version/*` 分支，方便在 GitHub 网页端切换浏览。
- `main` 始终保留最新开发状态，不要求目录中只存在某一个历史版本。
- `docs/planning/` 保存路线规划，可能描述未来版本能力，不等于当前代码已经全部实现。
- `runs/` 中的原始实验输出不提交到 Git，关键实验结果整理到 `docs/experiments/`。
