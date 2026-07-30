# AgentLite 版本与分支说明

## 当前版本

- 正式版本：`0.5.15`
- 比赛分支：`competition-submission`
- Python 包：`multi-agent-collaboration-runtime`
- 许可证：Apache-2.0

版本、实现文件和验证入口的详细对应关系见
`docs/planning/version-implementation-mapping.md`。

## 版本规则

项目版本遵循 `主版本.次版本.修订版本`：

- 主版本表示不兼容的公共接口变化；
- 次版本表示向后兼容的能力扩展；
- 修订版本表示兼容的修复和发行加固；
- `rcN` 表示发布候选，只用于冻结验收，不替代正式版本。

开发阶段使用 `v5.12x`、`v5.14f` 等工程标识记录连续迭代。字母后缀表示
同一阶段内的机制或实验增量，不直接对应 Python 包版本。

## 分支用途

| 分支或引用 | 用途 |
|---|---|
| `competition-submission` | 当前比赛提交、安装和复现入口 |
| `origin/competition-submission` | GitHub 上的比赛分支 |
| Git 标签 | 保存发布候选或正式发行的不可变引用 |
| Git 提交历史 | 证明设计、实现、实验和修复的完整开发过程 |

比赛分支保留当前有效的中文展示文档、问题设计、实验执行程序和最终发行材料。
已归档的中间叙述仍可通过 Git 历史查看，不通过重写历史隐藏。

## 发行身份

以下位置必须保持版本一致：

- `pyproject.toml` 中的项目版本；
- `agent_runtime/__init__.py` 中的运行时版本；
- `agentlite version` 的 CLI 输出；
- wheel 和 sdist 的文件名及 Core Metadata；
- 最终验收报告记录的 release version。

## 发行验证

正式版本只有在以下门禁同时通过时才成立：

1. 完整 Python 单元测试；
2. wheel 内容与独立安装检查；
3. sdist 内容、隔离安装与导入来源检查；
4. AutoGen Team/Core 无 Provider smoke；
5. CLI 版本与诊断检查；
6. Apache-2.0 源码、包内容和元数据一致性；
7. openEuler 最终验收及制品 SHA256 校验。

最终入口：

```bash
bash experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh
```

## 文档与证据

- 比赛交付：`docs/competition/v0.5.15-delivery-guide.md`
- 双任务设计：`docs/competition/AB_EXPERIMENT_DESIGN.md`
- 展示版问题：`docs/problems/A.md`、`docs/problems/B.md`
- 当前结果：`docs/competition/RESULTS_SNAPSHOT.md`
- 开发历程：`docs/competition/DEVELOPMENT_RECORD.md`
- 最终发行：`docs/release/v0.5.15-final-release-notes.md`

运行日志、Provider 凭据、临时分析和本地归档不得进入发行包。历史实验的完整
演进通过 Git 提交记录追溯，当前结论只以当前适用的验证报告为准。
