from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.core.models import TaskSpec


@dataclass(frozen=True, slots=True)
class DeliverableSchema:
    schema_id: str
    title: str
    required_sections: list[str]
    required_fields: list[str]
    field_aliases: dict[str, list[str]]
    instruction: str


TRAVEL_FINAL_SCHEMA = DeliverableSchema(
    schema_id="schema.travel.final_handbook.v1",
    title="最终旅行手册",
    required_sections=[
        "最终方案摘要",
        "每日行程表",
        "预算表",
        "交通与时间安排",
        "餐饮与禁忌",
        "雨天/低风险备选",
        "决策日志",
    ],
    required_fields=[
        "itinerary_table",
        "budget_table",
        "selected_destination",
        "duration",
        "day1_plan",
        "day2_plan",
        "day3_plan",
        "total_budget",
        "transport_plan",
        "lodging_plan",
        "local_food_plan",
        "non_spicy_option",
        "souvenir_budget",
        "weather_fallback",
        "motion_sickness_guard",
        "decision_log",
    ],
    field_aliases={
        "itinerary_table": ["每日行程表", "行程表", "每日安排"],
        "budget_table": ["预算表", "预算明细", "费用表"],
        "selected_destination": ["目的地", "选择", "最终方案"],
        "duration": ["3 天 2 晚", "三天两晚", "3天2晚"],
        "day1_plan": ["第一天", "Day 1", "D1"],
        "day2_plan": ["第二天", "Day 2", "D2"],
        "day3_plan": ["第三天", "Day 3", "D3"],
        "total_budget": ["总预算", "总计", "2600"],
        "transport_plan": ["交通", "出发", "换乘"],
        "lodging_plan": ["住宿", "休息"],
        "local_food_plan": ["当地特色餐", "特色餐", "餐饮"],
        "non_spicy_option": ["不吃辣", "非辣", "忌辣"],
        "souvenir_budget": ["伴手礼", "200 元", "200元"],
        "weather_fallback": ["雨天", "备选", "低风险"],
        "motion_sickness_guard": ["晕车", "盘山路"],
        "decision_log": ["决策日志", "修改原因", "修订日志"],
    },
    instruction=(
        "必须生成可直接执行的最终旅行手册。每日行程表至少包含上午/下午/晚上安排；"
        "预算表必须列出交通、住宿、餐饮、活动、伴手礼和总计；"
        "决策日志必须逐条说明 A6-A9 修订如何不破坏 A1 初始偏好。"
    ),
)


SECURITY_FINAL_SCHEMA = DeliverableSchema(
    schema_id="schema.security.final_audit.v1",
    title="最终合成安全审计手册",
    required_sections=[
        "最终链路图谱",
        "证据表",
        "风险类型",
        "过期线索处理",
        "预算受限策略",
        "系统决策日志",
        "后续审计建议",
    ],
    required_fields=[
        "chain_graph",
        "evidence_table",
        "initial_alert",
        "network_clue",
        "payload_mapping",
        "ledger_path",
        "entity_profile",
        "risk_type",
        "evidence_refs",
        "deprecated_clue_policy",
        "budget_limited_access_policy",
        "decision_log",
    ],
    field_aliases={
        "chain_graph": ["链路图谱", "审计链路", "链路"],
        "evidence_table": ["证据表", "证据清单", "证据链"],
        "initial_alert": ["初始告警", "alert_071", "Node_71"],
        "network_clue": ["网络线索", "proxy_token_PX42", "network"],
        "payload_mapping": ["payload 映射", "载荷映射", "ROUTE_VIA_ENTITY_085"],
        "ledger_path": ["账本路径", "ledger_path", "beneficiary_demo_999"],
        "entity_profile": ["实体画像", "entity_profile_085", "实体聚类"],
        "risk_type": ["风险类型", "synthetic_fraud_ring", "合规风险"],
        "evidence_refs": ["证据引用", "证据ID", "证据 ID", "evidence_refs", "ledger_"],
        "deprecated_clue_policy": ["过期线索", "deprecated", "device_shadow_17"],
        "budget_limited_access_policy": ["预算受限", "控制预算", "raw content"],
        "decision_log": ["系统决策日志", "决策日志", "处理策略"],
    },
    instruction=(
        "必须生成最终合成安全审计手册。证据表需要列出 B1-B9 的关键证据、"
        "状态来源和可信度；系统决策日志必须解释 B6 线索污染与 B8 预算下降时的处理策略。"
    ),
)


def schema_for_task(task: TaskSpec) -> DeliverableSchema | None:
    if not task.task_id.endswith("10"):
        return None
    group = task.group_id.lower()
    if task.task_id.startswith("A") or "travel" in group:
        return TRAVEL_FINAL_SCHEMA
    if task.task_id.startswith("B") or "security" in group:
        return SECURITY_FINAL_SCHEMA
    return None


def render_schema_prompt(schema: DeliverableSchema | None) -> str:
    if schema is None:
        return ""
    sections = "\n".join(f"- {item}" for item in schema.required_sections)
    fields = "\n".join(f"- {item}" for item in schema.required_fields)
    aliases = "\n".join(
        f"- {field}: {', '.join(items)}"
        for field, items in schema.field_aliases.items()
    )
    return (
        f"Final Deliverable Schema: {schema.schema_id}\n"
        f"标题: {schema.title}\n"
        f"必须包含章节:\n{sections}\n"
        f"必须覆盖字段:\n{fields}\n"
        f"字段中文语义提示:\n{aliases}\n"
        f"输出要求: {schema.instruction}"
    )


def schema_coverage(schema: DeliverableSchema | None, text: str) -> tuple[int, int]:
    hits, required, _ = schema_field_coverage(schema, text)
    return hits, required


def schema_field_coverage(
    schema: DeliverableSchema | None, text: str
) -> tuple[int, int, list[str]]:
    if schema is None:
        return (0, 0, [])
    normalized = text.lower()
    hits = 0
    missing: list[str] = []
    for field in schema.required_fields:
        variants = {
            field.lower(),
            field.lower().replace("_", " "),
            *[item.lower() for item in schema.field_aliases.get(field, [])],
        }
        if any(item in normalized for item in variants):
            hits += 1
            continue
        # Chinese task outputs often translate field names. Use section names as
        # a conservative fallback signal for schema completeness.
        keyword = field.split("_")[0].lower()
        if keyword and keyword in normalized:
            hits += 1
            continue
        missing.append(field)
    return hits, len(schema.required_fields), missing
