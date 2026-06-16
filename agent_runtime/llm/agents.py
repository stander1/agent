from __future__ import annotations

from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.llm.client import OpenAICompatibleChatClient


class LlmAgent:
    expects_runtime_prompt = True

    def __init__(
        self,
        *,
        agent_id: str,
        role: str,
        instruction: str,
        client: OpenAICompatibleChatClient,
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.instruction = instruction
        self.client = client

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        runtime_prompt = context[-1].content if context else task.prompt
        system_prompt = (
            f"你是 {self.role}，正在参与一个多 Agent 连续协作任务。\n"
            f"{self.instruction}\n"
            "要求：\n"
            "1. 输出中文。\n"
            "2. 保持结论可被下游 Agent 复用。\n"
            "3. 不要虚构实时网页数据，只能使用给定任务、上游上下文和本地资料。\n"
            "4. 尽量使用结构化小标题和列表，避免冗长寒暄。\n"
            "5. 输出控制在 180 到 260 个中文字符，MemoryManagerAgent 可控制在 120 到 180 个中文字符。"
        )
        user_prompt = (
            f"任务编号：{task.task_id}\n"
            f"任务标题：{task.title}\n"
            f"当前用户要求：{task.prompt}\n\n"
            f"运行时提供的上下文：\n{runtime_prompt}\n\n"
            f"请以 {self.role} 的职责完成本阶段输出。"
        )
        result = self.client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return AgentOutput(
            agent_id=self.agent_id,
            content=result.content,
            metadata={
                "llm": {
                    "model": result.model,
                    "usage": result.usage,
                    "latency_ms": result.latency_ms,
                    "finish_reason": result.raw_finish_reason,
                }
            },
        )


def build_mimo_travel_agents(client: OpenAICompatibleChatClient) -> list[LlmAgent]:
    return [
        LlmAgent(
            agent_id="planner",
            role="PlannerAgent",
            instruction="负责拆解任务目标、识别需要读取的状态和记忆，并给下游 Agent 明确执行计划。",
            client=client,
        ),
        LlmAgent(
            agent_id="retriever",
            role="Retriever/AnalyzerAgent",
            instruction="负责从本地资料和上游上下文中抽取约束、候选项、预算数据、审计证据或可排序的结构化信息。",
            client=client,
        ),
        LlmAgent(
            agent_id="writer",
            role="WriterAgent",
            instruction="负责生成面向用户的中间报告、对比表、方案草案或最终交付文档。",
            client=client,
        ),
        LlmAgent(
            agent_id="reviewer",
            role="ReviewerAgent",
            instruction="负责检查预算、时间、偏好和状态一致性，指出遗漏、冲突和需要修正的地方。",
            client=client,
        ),
        LlmAgent(
            agent_id="memory_manager",
            role="MemoryManagerAgent",
            instruction="负责把本轮可复用的偏好、证据、选择理由、修订策略或优化经验压缩成共享记忆摘要。",
            client=client,
        ),
    ]
