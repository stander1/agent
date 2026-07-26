# v5.14i 语义保真与冲突身份验收

该阶段根据 v5.14h 正式回归定位出的通用根因，验证：

1. 预算上限、预算总计和预算分项拥有不同 CCF 身份；
2. Markdown 预算表的分项事实不会被压成一个总预算值；
3. active 与 historical 值相同时不会制造错误冲突；
4. 规则只修复部分冲突跨度时不会误判为安全；
5. 最终答案必须满足最新用户任务中的强交付锚点；
6. 协议卫生统计覆盖模型输入、Agent 输出和团队最终输出；
7. 运行时代码中不存在 Question A/B 或实验地点专用逻辑。

该验收不调用 Provider。它先验证机制修复，再决定是否进入下一轮正式
A1-A10/B1-B10 Provider 回归。

openEuler 运行：

```bash
export AGENTLITE_V514I_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14i-semantic-fidelity-conflict-identity/run_openeuler.sh
```
