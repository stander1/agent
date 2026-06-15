from __future__ import annotations

from abc import ABC, abstractmethod

from agent_runtime.core.models import AgentOutput, TaskSpec


class DeterministicAgent(ABC):
    """Small deterministic agents for v0 benchmarking.

    These agents do not call an LLM. They create stable text so the benchmark
    runner can validate message accounting before optimization logic exists.
    """

    agent_id: str
    role: str

    @abstractmethod
    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        raise NotImplementedError


class PlannerAgent(DeterministicAgent):
    agent_id = "planner"
    role = "PlannerAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        steps = [
            "理解任务目标和输出要求",
            "检索与主题相关的材料和历史线索",
            "组织证据并生成结构化分析",
            "审查结论是否覆盖通信、状态、记忆三个方面",
        ]
        body = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(steps))
        content = (
            f"任务：{task.title}\n"
            f"用户需求：{task.prompt}\n"
            f"计划：\n{body}\n"
            "交接说明：请检索 Agent 按计划收集证据，并保留完整证据文本给后续 Agent。"
        )
        return AgentOutput(agent_id=self.agent_id, content=content)


class RetrieverAgent(DeterministicAgent):
    agent_id = "retriever"
    role = "RetrieverAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        prior = "\n\n".join(item.content for item in context)
        docs = task.documents or ["没有提供外部材料，使用任务描述作为唯一资料。"]
        evidence = "\n\n".join(
            f"证据 {idx + 1}：{doc}" for idx, doc in enumerate(docs)
        )
        content = (
            f"检索任务：{task.title}\n"
            f"上游上下文：\n{prior}\n\n"
            f"检索结果：\n{evidence}\n\n"
            "检索结论：以上证据将完整传递给写作 Agent，以便后续生成分析。"
        )
        return AgentOutput(agent_id=self.agent_id, content=content)


class WriterAgent(DeterministicAgent):
    agent_id = "writer"
    role = "WriterAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        prior = "\n\n".join(f"[{item.agent_id}]\n{item.content}" for item in context)
        content = (
            f"分析报告：{task.title}\n\n"
            f"任务原文：{task.prompt}\n\n"
            f"已接收的完整上下文如下：\n{prior}\n\n"
            "阶段性结论：\n"
            "1. 纯文本协作会让上游计划、检索证据和中间分析反复进入下游 prompt。\n"
            "2. 如果后续版本只传引用但仍读取完整原文，需要额外统计隐式读取成本。\n"
            "3. v0 的作用是保留这个高开销基线，供后续结构化通信和状态引用做对比。"
        )
        return AgentOutput(agent_id=self.agent_id, content=content)


class ReviewerAgent(DeterministicAgent):
    agent_id = "reviewer"
    role = "ReviewerAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        prior = "\n\n".join(f"[{item.agent_id}]\n{item.content}" for item in context)
        content = (
            f"审查结果：{task.title}\n\n"
            f"审查对象：\n{prior}\n\n"
            "审查意见：\n"
            "1. 当前 baseline_text_mode 保留了完整自然语言交接，可作为高开销对照组。\n"
            "2. 当前版本尚未真实使用 state_refs 和 memory_refs，这些字段仅作为后续版本接口预埋。\n"
            "3. 后续优化必须同时报告显式消息 token、Prompt View token、记忆读取 token 和控制开销。"
        )
        return AgentOutput(agent_id=self.agent_id, content=content)


def build_default_agents() -> list[DeterministicAgent]:
    return [PlannerAgent(), RetrieverAgent(), WriterAgent(), ReviewerAgent()]

