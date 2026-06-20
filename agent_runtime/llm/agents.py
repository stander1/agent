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
        is_runtime_lite = "runtime_lite 控制字段" in runtime_prompt
        final_task_instruction = ""
        length_instruction = (
            "5. 不设置人工输出长度上限；请根据当前职责完整回答。"
            "低开销协同由运行时通过 state_ref 和 Prompt View 控制，而不是压缩正文。\n"
        )
        contract_instruction = ""
        if is_runtime_lite:
            contract_instruction = (
                "\n6. 你必须使用 Agent Output Contract 输出，格式为：\n"
                "<CMJCC_CONTROL>\n"
                "{\"msg_type\":\"agent_output\",\"task_id\":\""
                f"{task.task_id}"
                "\",\"from\":\""
                f"{self.agent_id}"
                "\",\"action_completed\":\""
                f"{self.agent_id}_completed"
                "\",\"memory_card\":{\"summary\":\"短摘要\",\"key_points\":[],\"evidence_snippets\":[],\"tags\":[],"
                "\"reuse_scope\":[],\"confidence\":0.7,\"importance_hint\":0.6,\"coverage_score\":0.6,"
                "\"compression_loss_risk\":\"medium\",\"raw_required_hint\":false,"
                "\"sufficient_for_actions\":[],\"insufficient_for_actions\":[]},"
                "\"claim_cards\":[],\"handoff_suggestion\":{\"next_action\":\"continue\","
                "\"required_capabilities\":[],\"suggested_memory_refs\":[]},"
                "\"cost_report\":{\"output_tokens\":0,\"summary_tokens\":0,\"raw_pointer\":\"\"}}\n"
                "</CMJCC_CONTROL>\n"
                "<ARTIFACT>\n"
                "你的中文正文结果\n"
                "</ARTIFACT>\n"
                "控制头必须是合法 JSON；长正文只能放在 ARTIFACT 中。"
            )
        if task.task_id.endswith("10") or "最终" in task.title:
            final_task_instruction = (
                "\n当前任务是最终收束任务：必须输出可直接交付的完整结果，"
                "不要只输出方法论摘要。若上下文包含 Deliverable View，"
                "必须覆盖其中的关键结论、证据表、修订日志和决策日志。"
                "若上下文包含 Final Deliverable Schema，必须逐项覆盖 schema 的必需章节和字段，"
                "并在小标题或表格字段中显式保留关键 schema 字段名或中文字段标签。"
            )
            length_instruction = (
                "5. 最终收束任务必须完整输出可交付结果，不设置人工长度上限；"
                "如果内容较长，也必须优先保证字段完整、证据链完整和决策日志完整。\n"
            )
        system_prompt = (
            f"你是 {self.role}，正在参与一个多 Agent 连续协作任务。\n"
            f"{self.instruction}\n"
            f"{final_task_instruction}\n"
            "要求：\n"
            "1. 输出中文。\n"
            "2. 保持结论可被下游 Agent 复用。\n"
            "3. 不要虚构实时网页数据，只能使用给定任务、上游上下文和本地资料。\n"
            "4. 尽量使用结构化小标题和列表，避免冗长寒暄。\n"
            f"{length_instruction}"
            f"{contract_instruction}"
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
                    "provider_guard": result.provider_guard,
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
