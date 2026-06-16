# 版本控制策略

本项目采用“滚动主干 + 版本快照”的方式管理迭代。

GitHub 默认打开的是 `main` 分支的最新状态，所以 v0、v1 的代码和文档会在最新目录中同时出现。这不是最终版本边界，而是当前开发主干的状态。真正的版本边界由 Git tag 和 `version/*` 分支固定。

版本能力与创新方案的对照表见：[docs/planning/version-implementation-mapping.md](planning/version-implementation-mapping.md)。`v3.1`、`v3.2` 等小版本只表示主版本内部补强，不改变 `docs/planning/README.md` 中的 v0-v6 主路线。

## 分支与标签约定

| 类型 | 用途 | 是否继续开发 |
| --- | --- | --- |
| `main` | 当前最新开发主干，持续向 v2/v3 推进 | 是 |
| `version/v0-baseline` | v0 基线评测版本快照，便于在 GitHub 直接浏览 | 否 |
| `version/v1-runtime-lite` | v1 三线 Lite 闭环版本快照，便于在 GitHub 直接浏览 | 否 |
| `version/v2-llm-eval` | v2 真实 LLM 实验版本快照，便于在 GitHub 直接浏览 | 否 |
| `version/v2-longcontext-eval` | v2.1 长上下文真实 LLM 实验版本快照 | 否 |
| `version/v2-quality-rerun` | v2.2 长上下文质量重跑版本快照 | 否 |
| `version/v2-tiered-state` | v2.3 Hot/Warm/Cold 状态池分层版本快照 | 否 |
| `version/v3-memory-view-lite` | v3-lite 记忆复用与 Deliverable View 版本快照 | 否 |
| `version/v3-final-schema` | v3.1 Final Deliverable Schema 与 Reviewer Guard 版本快照 | 否 |
| `version/v3-reliability-guard` | v3.2 Runtime Output Reliability Guard 版本快照 | 否 |
| `v0.0-baseline` | v0 不可变标签 | 否 |
| `v1.0-runtime-lite` | v1 不可变标签 | 否 |
| `v2.0-llm-eval` | v2 不可变标签 | 否 |
| `v2.1-longcontext-eval` | v2.1 不可变标签 | 否 |
| `v2.2-quality-rerun` | v2.2 不可变标签 | 否 |
| `v2.3-tiered-state` | v2.3 不可变标签 | 否 |
| `v3.0-memory-view-lite` | v3-lite 不可变标签 | 否 |
| `v3.1-final-schema` | v3.1 不可变标签 | 否 |
| `v3.2-reliability-guard` | v3.2 不可变标签 | 否 |

## 当前版本边界

| 版本 | Git 引用 | 说明 |
| --- | --- | --- |
| v0 | `v0.0-baseline` / `version/v0-baseline` | 纯文本基线、tokenizer 统计、metrics/trace 输出、任务集与测试 |
| v1 | `v1.0-runtime-lite` / `version/v1-runtime-lite` | SHP-lite、StatePool-lite、MemoryStore-lite、端到端成本统计、v0/v1 实验记录 |
| v2 | `v2.0-llm-eval` / `version/v2-llm-eval` | MiMo v2.5 LLM 实验层、旅行任务组 A1-A5、真实模型对比结果 |
| v2.1 | `v2.1-longcontext-eval` / `version/v2-longcontext-eval` | A1-A10 长上下文实验、baseline 跨任务完整历史、主实验结果 |
| v2.2 | `v2.2-quality-rerun` / `version/v2-quality-rerun` | artifact 完整内容留档、网络重试、MiMo 质量裁判重跑 |
| v2.3 | `v2.3-tiered-state` / `version/v2-tiered-state` | Hot/Warm/Cold State Pool、Cold audit payload、Prompt/Audit 访问分离、分层指标 |
| v3-lite | `v3.0-memory-view-lite` / `version/v3-memory-view-lite` | PromotionView、ClaimCard、MemoryView、Alias Mapping、Deliverable View、v3 指标 |
| v3.1 | `v3.1-final-schema` / `version/v3-final-schema` | Final Deliverable Schema、中文字段覆盖、Reviewer 短上下文修复、A/B 最终交付质量记录 |
| v3.2 | `v3.2-reliability-guard` / `version/v3-reliability-guard` | Provider Response Guard、Agent Output Contract Guard、规则修复、degraded fallback、输出可靠性指标 |

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

查看 v2：

```powershell
git fetch --all --tags
git switch version/v2-llm-eval
```

查看 v2.1：

```powershell
git fetch --all --tags
git switch version/v2-longcontext-eval
```

查看 v2.2：

```powershell
git fetch --all --tags
git switch version/v2-quality-rerun
```

查看 v2.3：

```powershell
git fetch --all --tags
git switch version/v2-tiered-state
```

查看 v3-lite：

```powershell
git fetch --all --tags
git switch version/v3-memory-view-lite
```

查看 v3.1：

```powershell
git fetch --all --tags
git switch version/v3-final-schema
```

查看 v3.2：

```powershell
git fetch --all --tags
git switch version/v3-reliability-guard
```

回到最新开发主干：

```powershell
git switch main
```

也可以直接按 tag 查看不可变快照：

```powershell
git checkout v0.0-baseline
git checkout v1.0-runtime-lite
git checkout v2.0-llm-eval
git checkout v2.1-longcontext-eval
git checkout v2.2-quality-rerun
git checkout v2.3-tiered-state
git checkout v3.0-memory-view-lite
git checkout v3.1-final-schema
git checkout v3.2-reliability-guard
```

注意：直接 checkout tag 会进入 detached HEAD 状态，只适合查看或复现实验，不适合继续开发。

## 后续迭代规则

- 每完成一个可运行版本，就创建一个 tag，例如 `v2.0-contract-guard`。
- 每个比赛展示用版本，同时创建一个 `version/*` 分支，方便在 GitHub 网页端切换浏览。
- `main` 始终保留最新开发状态，不要求目录中只存在某一个历史版本。
- `docs/planning/` 保存路线规划，可能描述未来版本能力，不等于当前代码已经全部实现。
- `runs/` 中的原始实验输出不提交到 Git，关键实验结果整理到 `docs/experiments/`。
