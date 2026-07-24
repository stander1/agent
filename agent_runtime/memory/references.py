from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal


MemoryReferenceType = Literal["strong", "weak", "lineage", "evidence"]


@dataclass(slots=True)
class MemoryReferenceRecord:
    ref_id: str
    memory_id: str
    target_kind: str
    target_id: str
    ref_type: MemoryReferenceType
    status: str
    created_at: str
    tombstoned_at: str | None = None
    replacement_memory_id: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MemoryReferenceManagerLite:
    """Tracks strong/weak/lineage/evidence refs and replacement chains."""

    VALID_TYPES = {"strong", "weak", "lineage", "evidence"}

    def __init__(self) -> None:
        self._records: dict[str, MemoryReferenceRecord] = {}
        self._replacement_chain: dict[str, str] = {}

    def create(
        self,
        *,
        memory_id: str,
        target_kind: str,
        target_id: str,
        ref_type: MemoryReferenceType,
        reason: str = "",
    ) -> MemoryReferenceRecord:
        if ref_type not in self.VALID_TYPES:
            raise ValueError(f"Unknown memory reference type: {ref_type}")
        for record in self._records.values():
            if (
                record.memory_id == memory_id
                and record.target_kind == target_kind
                and record.target_id == target_id
                and record.ref_type == ref_type
                and record.status == "active"
            ):
                return record
        ref_id = f"mref_{len(self._records) + 1:06d}"
        record = MemoryReferenceRecord(
            ref_id=ref_id,
            memory_id=memory_id,
            target_kind=target_kind,
            target_id=target_id,
            ref_type=ref_type,
            status="active",
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )
        self._records[ref_id] = record
        return record

    def active_refs(
        self, memory_id: str, ref_type: MemoryReferenceType | None = None
    ) -> list[MemoryReferenceRecord]:
        return [
            record
            for record in self._records.values()
            if record.memory_id == memory_id
            and record.status == "active"
            and (ref_type is None or record.ref_type == ref_type)
        ]

    def tombstone_memory(
        self,
        memory_id: str,
        *,
        replacement_memory_id: str | None = None,
        reason: str = "",
    ) -> int:
        count = 0
        for record in self._records.values():
            if record.memory_id != memory_id:
                continue
            if record.status != "tombstoned":
                record.status = "tombstoned"
                record.tombstoned_at = datetime.now(timezone.utc).isoformat()
                count += 1
            record.replacement_memory_id = replacement_memory_id
            record.reason = reason or record.reason
        if replacement_memory_id:
            self._replacement_chain[memory_id] = replacement_memory_id
        return count

    def replace_memory(
        self, old_memory_id: str, new_memory_id: str, *, reason: str = ""
    ) -> int:
        return self.tombstone_memory(
            old_memory_id,
            replacement_memory_id=new_memory_id,
            reason=reason or "memory_replaced",
        )

    def replacement_chain(self, memory_id: str, max_depth: int = 8) -> list[str]:
        chain = [memory_id]
        current = memory_id
        seen = {memory_id}
        for _ in range(max_depth):
            next_memory_id = self._replacement_chain.get(current)
            if not next_memory_id or next_memory_id in seen:
                break
            chain.append(next_memory_id)
            seen.add(next_memory_id)
            current = next_memory_id
        return chain

    def snapshot(self) -> list[dict[str, object]]:
        return [record.to_dict() for record in self._records.values()]

    def restore(self, records: list[dict[str, object]]) -> int:
        restored = 0
        for payload in records:
            try:
                record = MemoryReferenceRecord(**payload)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            self._records[record.ref_id] = record
            if record.replacement_memory_id:
                self._replacement_chain[record.memory_id] = record.replacement_memory_id
            restored += 1
        return restored
