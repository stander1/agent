from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]")


@dataclass(slots=True)
class MemoryRef:
    memory_id: str
    version_id: int
    status: str
    task_topic: str


@dataclass(slots=True)
class MemoryObject:
    memory_id: str
    version_id: int
    task_id: str
    source_agent: str
    task_topic: str
    summary: str
    tags: list[str]
    created_at: str
    status: str = "active"
    hit_count: int = 0


class MemoryStoreLite:
    """Small in-memory v1 shared memory store."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryObject] = {}

    def write_memory(
        self,
        *,
        task_id: str,
        source_agent: str,
        task_topic: str,
        summary: str,
        tags: list[str],
    ) -> MemoryRef:
        memory_id = self._next_memory_id(task_id, source_agent, summary)
        memory = MemoryObject(
            memory_id=memory_id,
            version_id=1,
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=summary,
            tags=tags,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._memories[memory_id] = memory
        return self.ref(memory)

    def search_memory(
        self, query: str, tags: list[str] | None = None, top_k: int = 3
    ) -> list[MemoryRef]:
        query_terms = set(self._terms(query))
        requested_tags = set(tags or [])
        scored: list[tuple[int, MemoryObject]] = []
        for memory in self._memories.values():
            if memory.status != "active":
                continue
            memory_terms = set(self._terms(memory.summary + " " + " ".join(memory.tags)))
            overlap = len(query_terms & memory_terms)
            tag_overlap = len(requested_tags & set(memory.tags))
            score = overlap + tag_overlap * 3
            if score > 0:
                scored.append((score, memory))

        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        refs: list[MemoryRef] = []
        for _, memory in scored[:top_k]:
            memory.hit_count += 1
            refs.append(self.ref(memory))
        return refs

    def render_prompt_view(self, memory_ref: MemoryRef) -> str:
        memory = self._memories[memory_ref.memory_id]
        tag_text = ", ".join(memory.tags[:5])
        return (
            f"[memory:{memory.memory_id}@v{memory.version_id}] "
            f"{memory.summary} tags=[{tag_text}]"
        )

    def ref_to_dict(self, memory_ref: MemoryRef) -> dict:
        return asdict(memory_ref)

    def ref(self, memory: MemoryObject) -> MemoryRef:
        return MemoryRef(
            memory_id=memory.memory_id,
            version_id=memory.version_id,
            status=memory.status,
            task_topic=memory.task_topic,
        )

    def _next_memory_id(self, task_id: str, source_agent: str, summary: str) -> str:
        seed = f"{task_id}:{source_agent}:{summary}:{len(self._memories)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"mem_{digest}"

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [item.lower() for item in _WORD_RE.findall(text)]

