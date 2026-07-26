from __future__ import annotations

import hashlib
import re
from typing import Any


_IDENTIFIER = r"[A-Za-z][A-Za-z0-9_.:/-]{2,}"
_KEY_VALUE_RE = re.compile(
    rf"(?P<key>[A-Za-z][A-Za-z0-9_]{{2,48}})\s*[:=]\s*"
    rf"(?P<value>\[[^\]\n]{{1,800}}\]|\{{[^}}\n]{{1,1600}}\}}|"
    rf"{_IDENTIFIER}|-?\d+(?:\.\d+)?)"
    rf"(?=\s*(?:;|\n|$))"
)
_SCORE_PAIR_RE = re.compile(
    rf"(?P<identifier>{_IDENTIFIER})\s*[:=]\s*(?P<score>0(?:\.\d+)?|1(?:\.0+)?)"
)
_LIST_ITEM_RE = re.compile(_IDENTIFIER)


def structured_state_payloads(
    text: str,
    *,
    semantic_action: str,
) -> list[dict[str, Any]]:
    """Extract state references from a real framework result.

    This function does not synthesize embeddings or retrieval hits. It only
    converts identifiers and scores already present in the framework output
    into a structured state payload with an audit digest.
    """

    body = str(text or "").strip()
    if not body:
        return []
    action = str(semantic_action or "").strip().upper()
    fields = _structured_fields(body)
    payloads: list[dict[str, Any]] = []

    if action == "BUILD_EMBEDDING" or re.search(
        r"\bembedding_state\b", body, re.IGNORECASE
    ):
        embedding = _embedding_payload(body, fields)
        if embedding is not None:
            payloads.append(
                {
                    "state_type": "embedding_state",
                    "payload": embedding,
                    "summary": _state_summary(
                        "embedding",
                        embedding.get("query_embedding_id", ""),
                        len(embedding.get("chunk_embedding_ids", [])),
                    ),
                    "usage_hint": "vector_similarity_scoring",
                    "contains_embedding_refs": True,
                    "tier": "hot",
                    "access_policy": "metadata_view_only",
                }
            )

    if action == "RETRIEVE_EVIDENCE" or re.search(
        r"\bretrieval_state\b", body, re.IGNORECASE
    ):
        retrieval = _retrieval_payload(body, fields)
        if retrieval is not None:
            payloads.append(
                {
                    "state_type": "retrieval_state",
                    "payload": retrieval,
                    "summary": _state_summary(
                        "retrieval",
                        retrieval.get("query_embedding_id", ""),
                        len(retrieval.get("chunk_ids", [])),
                    ),
                    "usage_hint": "summary_context_selection",
                    "contains_embedding_refs": bool(
                        retrieval.get("query_embedding_id")
                        or retrieval.get("chunk_embedding_ids")
                    ),
                    "tier": "hot",
                    "access_policy": "prompt_view_only",
                }
            )
    return payloads


def _structured_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _KEY_VALUE_RE.finditer(text):
        fields[match.group("key").casefold()] = match.group("value").strip()
    return fields


def _embedding_payload(
    text: str,
    fields: dict[str, str],
) -> dict[str, Any] | None:
    query_embedding_id = _first_identifier(
        fields.get("query_embedding_id", "")
    )
    vector_dim = _first_integer(fields.get("vector_dim", ""))
    score_map = _score_map(
        fields.get("similarity_scores", "")
        or fields.get("score_map", "")
        or text
    )
    candidates = _identifier_list(
        fields.get("chunk_embedding_ids", "")
        or fields.get("candidates", "")
        or fields.get("top_k", "")
    )
    if not query_embedding_id or vector_dim is None or not score_map:
        return None
    if not candidates:
        candidates = list(score_map)
    return {
        "query_embedding_id": query_embedding_id,
        "chunk_embedding_ids": candidates,
        "chunk_ids": list(score_map),
        "source_ids": _source_ids(fields),
        "vector_dim": vector_dim,
        "similarity_scores": [score_map[key] for key in score_map],
        "score_map": score_map,
        "payload_kind": "structured_non_text",
        "extraction_method": "framework_output_key_value_v1",
        "raw_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _retrieval_payload(
    text: str,
    fields: dict[str, str],
) -> dict[str, Any] | None:
    score_map = _score_map(
        fields.get("similarity_scores", "")
        or fields.get("score_map", "")
        or text
    )
    chunk_ids = _identifier_list(
        fields.get("chunk_ids", "")
        or fields.get("evidence_rows", "")
        or fields.get("evidence_refs", "")
        or fields.get("top_k", "")
    )
    source_ids = _source_ids(fields)
    if not chunk_ids:
        chunk_ids = list(score_map)
    if not chunk_ids and not source_ids:
        return None
    query_embedding_id = _first_identifier(
        fields.get("query_embedding_id", "")
    )
    chunk_embedding_ids = _identifier_list(
        fields.get("chunk_embedding_ids", "")
    )
    return {
        "chunk_ids": chunk_ids,
        "source_ids": source_ids,
        "score_map": score_map,
        "evidence_rank": list(score_map) or chunk_ids,
        "query_embedding_id": query_embedding_id or None,
        "chunk_embedding_ids": chunk_embedding_ids,
        "vector_dim": _first_integer(fields.get("vector_dim", "")),
        "payload_kind": "structured_non_text",
        "contains_embedding_refs": bool(
            query_embedding_id or chunk_embedding_ids
        ),
        "extraction_method": "framework_output_key_value_v1",
        "raw_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _source_ids(fields: dict[str, str]) -> list[str]:
    return _identifier_list(
        fields.get("source_ids", "")
        or fields.get("evidence_refs", "")
        or fields.get("evidence_rows", "")
    )


def _score_map(text: str) -> dict[str, float]:
    return {
        match.group("identifier"): float(match.group("score"))
        for match in _SCORE_PAIR_RE.finditer(str(text or ""))
    }


def _identifier_list(text: str) -> list[str]:
    return list(dict.fromkeys(_LIST_ITEM_RE.findall(str(text or ""))))


def _first_identifier(text: str) -> str:
    match = _LIST_ITEM_RE.search(str(text or ""))
    return match.group(0) if match else ""


def _first_integer(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d+)(?!\d)", str(text or ""))
    return int(match.group(1)) if match else None


def _state_summary(kind: str, query_id: str, item_count: int) -> str:
    query = query_id or "none"
    return (
        f"AutoGen {kind} structured state: query_ref={query}; "
        f"item_count={item_count}."
    )
