from __future__ import annotations

import hashlib
from typing import Any

from agent_runtime.core.models import TaskSpec


VECTOR_DIM = 384
EMBEDDING_MODEL = "deterministic_embedding_stub_v1"


def build_embedding_state_payload(task: TaskSpec) -> dict[str, Any]:
    chunks = _chunk_records(task)
    chunk_ids = [item["chunk_id"] for item in chunks]
    chunk_embedding_ids = [_embedding_id(task.task_id, item) for item in chunk_ids]
    similarity_scores = [_score(index) for index, _ in enumerate(chunks)]
    return {
        "query_embedding_id": f"emb_{task.task_id}_query",
        "chunk_embedding_ids": chunk_embedding_ids,
        "chunk_ids": chunk_ids,
        "source_ids": [item["source_id"] for item in chunks],
        "vector_dim": VECTOR_DIM,
        "embedding_model": EMBEDDING_MODEL,
        "vector_store_ref": f"vector://runtime/{task.task_id}",
        "similarity_scores": similarity_scores,
        "score_map": dict(zip(chunk_ids, similarity_scores, strict=False)),
        "payload_kind": "structured_non_text",
    }


def build_retrieval_state_payload(
    task: TaskSpec, *, embedding_state_id: str | None = None
) -> dict[str, Any]:
    chunks = _chunk_records(task)
    chunk_map = {item["chunk_id"]: item for item in chunks}
    embedding_payload = build_embedding_state_payload(task)
    score_map = embedding_payload["score_map"]
    evidence_rank = sorted(chunk_map, key=lambda chunk_id: score_map[chunk_id], reverse=True)
    payload = {
        "chunk_ids": evidence_rank,
        "source_ids": [chunk_map[item]["source_id"] for item in evidence_rank],
        "score_map": score_map,
        "evidence_rank": evidence_rank,
        "chunks": chunk_map,
        "query_embedding_id": embedding_payload["query_embedding_id"],
        "chunk_embedding_ids": embedding_payload["chunk_embedding_ids"],
        "vector_dim": embedding_payload["vector_dim"],
        "similarity_scores": [
            score_map[item] for item in evidence_rank
        ],
        "vector_store_ref": embedding_payload["vector_store_ref"],
        "contains_embedding_refs": True,
        "payload_kind": "structured_non_text",
    }
    if embedding_state_id:
        payload["embedding_state_id"] = embedding_state_id
    return payload


def _chunk_records(task: TaskSpec) -> list[dict[str, str]]:
    docs = task.documents or [task.prompt]
    records = []
    for idx, doc in enumerate(docs, start=1):
        chunk_id = f"{task.task_id}_chunk_{idx}"
        records.append(
            {
                "chunk_id": chunk_id,
                "source_id": f"{task.task_id}_source_{idx}",
                "text": doc,
            }
        )
    return records


def _embedding_id(task_id: str, chunk_id: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{chunk_id}".encode("utf-8")).hexdigest()[:12]
    return f"emb_{digest}"


def _score(index: int) -> float:
    return round(max(0.1, 1.0 - index * 0.12), 4)
