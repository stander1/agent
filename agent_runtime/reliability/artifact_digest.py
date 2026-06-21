from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
from typing import Any


def build_typed_artifact_digest(artifact: str) -> dict[str, Any]:
    compact = " ".join(artifact.split())
    artifact_type = detect_artifact_type(artifact)
    digest: dict[str, Any] = {
        "artifact_type": artifact_type,
        "detected_headings": markdown_headings(artifact),
        "summary": compact[:240],
        "content_hash": hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
        "content_chars": len(artifact),
        "digest_schema": "typed_artifact_digest.v1-lite",
        "quality_flags": [],
    }

    if artifact_type == "python_code":
        digest.update(python_digest(artifact))
    elif artifact_type == "json":
        digest.update(json_digest(artifact))
    elif artifact_type == "terminal_log":
        digest.update(terminal_log_digest(artifact))
    elif artifact_type == "csv_table":
        digest.update(table_digest(artifact))
    elif artifact_type == "markdown":
        digest.update(markdown_digest(artifact))
    else:
        digest["key_points"] = sentence_points(artifact)

    if not digest.get("summary"):
        digest["quality_flags"].append("empty_summary")
    if artifact_type == "natural_language" and len(compact) > 1200:
        digest["quality_flags"].append("long_untyped_artifact")
    return digest


def detect_artifact_type(artifact: str) -> str:
    stripped = artifact.strip()
    if not stripped:
        return "natural_language"
    if looks_like_json(stripped):
        return "json"
    if looks_like_python(stripped):
        return "python_code"
    if looks_like_terminal_log(stripped):
        return "terminal_log"
    if looks_like_csv_table(stripped):
        return "csv_table"
    if markdown_headings(stripped):
        return "markdown"
    return "natural_language"


def python_digest(artifact: str) -> dict[str, Any]:
    try:
        tree = ast.parse(artifact)
    except SyntaxError as exc:
        return {
            "parse_status": "syntax_error",
            "syntax_error": str(exc),
            "imports": [],
            "function_signatures": [],
            "classes": [],
            "entrypoints": [],
            "quality_flags": ["python_parse_failed"],
        }

    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    entrypoints: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [arg.arg for arg in node.args.args]
            functions.append(f"{node.name}({', '.join(args)})")
            if node.name in {"main", "run", "handler"}:
                entrypoints.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    return {
        "parse_status": "ok",
        "imports": imports[:12],
        "function_signatures": functions[:12],
        "classes": classes[:12],
        "entrypoints": entrypoints[:8],
    }


def json_digest(artifact: str) -> dict[str, Any]:
    try:
        parsed = json.loads(artifact)
    except json.JSONDecodeError as exc:
        return {
            "parse_status": "json_error",
            "json_error": str(exc),
            "top_level_type": "unknown",
            "top_keys": [],
            "field_types": {},
            "quality_flags": ["json_parse_failed"],
        }

    if isinstance(parsed, dict):
        keys = list(parsed.keys())
        return {
            "parse_status": "ok",
            "top_level_type": "object",
            "top_keys": keys[:20],
            "field_types": {key: type(parsed[key]).__name__ for key in keys[:20]},
        }
    if isinstance(parsed, list):
        return {
            "parse_status": "ok",
            "top_level_type": "array",
            "row_count": len(parsed),
            "sample_item_type": type(parsed[0]).__name__ if parsed else "empty",
        }
    return {
        "parse_status": "ok",
        "top_level_type": type(parsed).__name__,
        "top_keys": [],
        "field_types": {},
    }


def terminal_log_digest(artifact: str) -> dict[str, Any]:
    lines = artifact.splitlines()
    error_patterns = [
        line.strip()
        for line in lines
        if re.search(r"\b(error|exception|traceback|failed|fatal)\b", line, re.I)
    ]
    exit_code = None
    exit_match = re.search(r"(?:exit code|return code|退出码)\D+(-?\d+)", artifact, re.I)
    if exit_match:
        exit_code = int(exit_match.group(1))
    return {
        "line_count": len(lines),
        "exit_code": exit_code,
        "error_patterns": error_patterns[:8],
        "tail": lines[-5:],
    }


def table_digest(artifact: str) -> dict[str, Any]:
    sample = artifact[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(sample), dialect)
    rows = [row for row in reader if row]
    header = rows[0] if rows else []
    data_rows = rows[1:]
    return {
        "column_names": header[:24],
        "column_count": len(header),
        "sample_row_count": len(data_rows),
        "sample_rows": data_rows[:3],
    }


def markdown_digest(artifact: str) -> dict[str, Any]:
    headings = markdown_headings(artifact)
    decisions = [
        line.strip("-* 　")
        for line in artifact.splitlines()
        if re.search(r"(结论|建议|decision|todo|risk|风险)", line, re.I)
    ]
    return {
        "heading_outline": headings[:12],
        "decision_lines": decisions[:10],
        "section_count": len(headings),
    }


def markdown_headings(text: str) -> list[str]:
    return re.findall(r"^\s{0,3}#{1,4}\s+(.+)$", text, re.M)[:12]


def sentence_points(text: str) -> list[str]:
    parts = re.split(r"[。！？.!?]\s*", text.strip())
    return [part.strip() for part in parts if part.strip()][:5]


def looks_like_json(text: str) -> bool:
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return False
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def looks_like_python(text: str) -> bool:
    if re.search(r"^\s*(def|class|import|from)\s+", text, re.M):
        return True
    return "if __name__ == \"__main__\"" in text or "if __name__ == '__main__'" in text


def looks_like_terminal_log(text: str) -> bool:
    return bool(
        re.search(r"\b(traceback|exception|error|failed|exit code|return code)\b", text, re.I)
        and len(text.splitlines()) >= 2
    )


def looks_like_csv_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    first = lines[0]
    if "," not in first and "\t" not in first:
        return False
    delimiter = "," if "," in first else "\t"
    first_count = len(first.split(delimiter))
    if first_count < 2:
        return False
    return sum(1 for line in lines[:5] if len(line.split(delimiter)) == first_count) >= 2
