from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent_runtime.core.models import TaskSpec


@dataclass(frozen=True, slots=True)
class DeliverableSchema:
    schema_id: str
    title: str
    required_sections: list[str]
    required_fields: list[str]
    field_aliases: dict[str, list[str]]
    instruction: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DeliverableSchema":
        return cls(
            schema_id=str(payload["schema_id"]),
            title=str(payload.get("title", "Final Deliverable")),
            required_sections=[
                str(item) for item in payload.get("required_sections", [])
            ],
            required_fields=[
                str(item) for item in payload.get("required_fields", [])
            ],
            field_aliases={
                str(field): [str(alias) for alias in aliases]
                for field, aliases in dict(payload.get("field_aliases", {})).items()
            },
            instruction=str(payload.get("instruction", "")),
        )


def schema_for_task(task: TaskSpec) -> DeliverableSchema | None:
    """Return an explicitly configured schema without inferring the task domain."""
    payload = task.metadata.get("deliverable_schema")
    if payload is None:
        return None
    if isinstance(payload, DeliverableSchema):
        return payload
    if not isinstance(payload, Mapping):
        raise TypeError("task metadata deliverable_schema must be a mapping")
    return DeliverableSchema.from_mapping(payload)


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
        f"Title: {schema.title}\n"
        f"Required sections:\n{sections}\n"
        f"Required fields:\n{fields}\n"
        f"Field aliases:\n{aliases}\n"
        f"Output requirements: {schema.instruction}"
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
        keyword = field.split("_")[0].lower()
        if keyword and keyword in normalized:
            hits += 1
            continue
        missing.append(field)
    return hits, len(schema.required_fields), missing
