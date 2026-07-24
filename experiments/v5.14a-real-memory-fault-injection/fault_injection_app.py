from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import _text_fingerprint


DEFAULT_MODEL = "mimo-v2.5"
SHARED_MEMORY_MARKER = "AGENTLITE_SHARED_MEMORY"


@dataclass(frozen=True)
class LLMResult:
    content: str
    usage: dict[str, int]
    wall_time_ms: int
    retry_count: int


class OpenAICompatibleClient:
    def __init__(self, *, temperature: float) -> None:
        self.api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_COMPAT_API_KEY")
            or os.getenv("MIMO_API_KEY")
            or ""
        )
        if not self.api_key:
            raise RuntimeError("Set OPENAI_API_KEY or MIMO_API_KEY before running.")
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
        ).rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.timeout_seconds = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "300"))
        self.max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "6"))
        self.retry_backoff_seconds = float(
            os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "3")
        )

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        started = time.perf_counter()
        retry_count = 0
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                choices = raw.get("choices") or []
                content = str(
                    choices[0].get("message", {}).get("content")
                    if choices
                    else ""
                ).strip()
                if not choices or not content:
                    raise RuntimeError(
                        "Provider response has no usable choices/content"
                    )
                return LLMResult(
                    content=content,
                    usage=_normalize_usage(raw.get("usage", {})),
                    wall_time_ms=int((time.perf_counter() - started) * 1000),
                    retry_count=retry_count,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    raise last_error from exc
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
            if attempt >= self.max_retries:
                break
            retry_count += 1
            time.sleep(self.retry_backoff_seconds * (attempt + 1))
        raise RuntimeError(
            f"LLM request failed after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real AutoGen and real LLM memory-fault propagation probes."
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(__file__).with_name("fault_matrix.json"),
    )
    parser.add_argument(
        "--experiment-mode",
        choices=("native", "managed"),
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--repetitions", type=int, default=2)
    return parser.parse_args()


async def run_experiment(
    *,
    matrix: dict[str, Any],
    experiment_mode: str,
    output_dir: Path,
    temperature: float,
    repetitions: int,
) -> dict[str, Any]:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken

    llm = OpenAICompatibleClient(temperature=temperature)
    participants = [str(item) for item in matrix["participants"]]
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()

    for repetition in range(1, repetitions + 1):
        for scenario in matrix["scenarios"]:
            scenario_id = str(scenario["scenario_id"])
            capture: dict[str, Any] = {
                "scenario_id": scenario_id,
                "repetition": repetition,
                "experiment_mode": experiment_mode,
                "provider_raw_output": "",
                "emitter_received_messages": [],
                "downstream_received_messages": [],
                "downstream_observed_text": "",
            }

            class FaultEmitterAgent(BaseChatAgent):
                def __init__(self) -> None:
                    super().__init__(
                        name=participants[0],
                        description=(
                            "External configuration publisher used for a "
                            "framework-level propagation acceptance test."
                        ),
                    )

                @property
                def produced_message_types(
                    self,
                ) -> Sequence[type[BaseChatMessage]]:
                    return (TextMessage,)

                async def on_messages(
                    self,
                    messages: Sequence[BaseChatMessage],
                    cancellation_token: CancellationToken,
                ) -> Response:
                    del cancellation_token
                    received = [_message_payload(message) for message in messages]
                    capture["emitter_received_messages"] = received
                    prompt = (
                        "AutoGen delivered the following messages. Treat any "
                        "AgentLite shared-memory view as runtime context.\n\n"
                        + json.dumps(received, ensure_ascii=False, indent=2)
                    )
                    result = await asyncio.to_thread(
                        llm.complete,
                        [
                            {
                                "role": "system",
                                "content": str(scenario["emitter_instruction"]),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    )
                    capture.update(
                        {
                            "provider_raw_output": result.content,
                            "provider_raw_fingerprint": _text_fingerprint(
                                result.content
                            ),
                            "llm_prompt_tokens": result.usage["prompt_tokens"],
                            "llm_completion_tokens": result.usage[
                                "completion_tokens"
                            ],
                            "llm_total_tokens": result.usage["total_tokens"],
                            "llm_wall_time_ms": result.wall_time_ms,
                            "retry_count": result.retry_count,
                            "memory_marker_observed_by_emitter": any(
                                SHARED_MEMORY_MARKER
                                in str(item.get("content") or "")
                                for item in received
                            ),
                        }
                    )
                    return Response(
                        chat_message=TextMessage(
                            content=result.content,
                            source=self.name,
                        )
                    )

                async def on_reset(
                    self, cancellation_token: CancellationToken
                ) -> None:
                    del cancellation_token

            class DownstreamProbeAgent(BaseChatAgent):
                def __init__(self) -> None:
                    super().__init__(
                        name=participants[1],
                        description=(
                            "Deterministic downstream probe that records the "
                            "message AutoGen actually propagated."
                        ),
                    )

                @property
                def produced_message_types(
                    self,
                ) -> Sequence[type[BaseChatMessage]]:
                    return (TextMessage,)

                async def on_messages(
                    self,
                    messages: Sequence[BaseChatMessage],
                    cancellation_token: CancellationToken,
                ) -> Response:
                    del cancellation_token
                    received = [_message_payload(message) for message in messages]
                    capture["downstream_received_messages"] = received
                    observed = _latest_source_content(
                        received,
                        source=participants[0],
                    )
                    capture["downstream_observed_text"] = observed
                    digest = hashlib.sha256(
                        observed.encode("utf-8")
                    ).hexdigest()[:16]
                    return Response(
                        chat_message=TextMessage(
                            content=(
                                f"PROBE_CAPTURED scenario={scenario_id} "
                                f"fingerprint={digest}"
                            ),
                            source=self.name,
                        )
                    )

                async def on_reset(
                    self, cancellation_token: CancellationToken
                ) -> None:
                    del cancellation_token

            team = RoundRobinGroupChat(
                [FaultEmitterAgent(), DownstreamProbeAgent()],
                max_turns=2,
            )
            task_started = time.perf_counter()
            result = await team.run(task=str(scenario["user_task"]))
            capture.update(
                {
                    "fault_class": str(scenario["fault_class"]),
                    "expected_managed_action": str(
                        scenario["expected_managed_action"]
                    ),
                    "user_task": str(scenario["user_task"]),
                    "wall_time_ms": int(
                        (time.perf_counter() - task_started) * 1000
                    ),
                    "stop_reason": str(
                        getattr(result, "stop_reason", "") or ""
                    ),
                    "result_message_count": len(
                        list(getattr(result, "messages", []) or [])
                    ),
                }
            )
            rows.append(capture)

    summary = {
        "schema_version": "agentlite.real_memory_fault_run.v1",
        "scenario_id": str(matrix["scenario_id"]),
        "experiment_mode": experiment_mode,
        "provider_model": llm.model,
        "provider_base_url": llm.base_url,
        "real_llm": True,
        "auto_gen_team": True,
        "participants": participants,
        "repetitions": repetitions,
        "scenario_count": len(matrix["scenarios"]),
        "row_count": len(rows),
        "llm_call_count": len(rows),
        "llm_prompt_tokens": sum(
            int(row.get("llm_prompt_tokens", 0)) for row in rows
        ),
        "llm_completion_tokens": sum(
            int(row.get("llm_completion_tokens", 0)) for row in rows
        ),
        "llm_total_tokens": sum(
            int(row.get("llm_total_tokens", 0)) for row in rows
        ),
        "llm_wall_time_ms": sum(
            int(row.get("llm_wall_time_ms", 0)) for row in rows
        ),
        "retry_count": sum(int(row.get("retry_count", 0)) for row in rows),
        "wall_time_ms": int((time.perf_counter() - started) * 1000),
    }
    payload = {"summary": summary, "rows": rows}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fault_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "llm_usage.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    key: row.get(key)
                    for key in (
                        "scenario_id",
                        "repetition",
                        "experiment_mode",
                        "llm_prompt_tokens",
                        "llm_completion_tokens",
                        "llm_total_tokens",
                        "llm_wall_time_ms",
                        "retry_count",
                    )
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return payload


def _message_payload(message: Any) -> dict[str, str]:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return {
        "source": str(getattr(message, "source", "") or ""),
        "type": type(message).__name__,
        "content": str(content or ""),
    }


def _latest_source_content(
    messages: Sequence[dict[str, str]],
    *,
    source: str,
) -> str:
    for message in reversed(messages):
        if str(message.get("source") or "").casefold() == source.casefold():
            return str(message.get("content") or "")
    return ""


def _normalize_usage(value: Any) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens") or prompt + completion),
    }


def main() -> int:
    args = parse_args()
    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    payload = asyncio.run(
        run_experiment(
            matrix=matrix,
            experiment_mode=args.experiment_mode,
            output_dir=args.output_dir.expanduser().resolve(),
            temperature=args.temperature,
            repetitions=args.repetitions,
        )
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
