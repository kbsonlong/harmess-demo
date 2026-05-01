import json
import time
from pathlib import Path
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler


def _format_message_content(content: Any) -> str:
    try:
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)
    except Exception:
        return str(content)


class ToolEventPrinter(BaseCallbackHandler):
    def __init__(self):
        self._start_time_by_run_id = {}

    def on_tool_start(
        self,
        serialized,
        input_str=None,
        inputs=None,
        run_id=None,
        parent_run_id=None,
        **kwargs,
    ):
        name = None
        if isinstance(serialized, dict):
            name = serialized.get("name") or serialized.get("id")
        name = name or "unknown_tool"
        payload = inputs if inputs is not None else input_str
        if run_id is not None:
            self._start_time_by_run_id[run_id] = time.perf_counter()
        print(
            f"\n[tool:start] {name} run_id={run_id} parent_run_id={parent_run_id}\n{_format_message_content(payload)}\n"
        )

    def on_tool_end(self, output, run_id=None, parent_run_id=None, **kwargs):
        duration_s = None
        if run_id is not None:
            start = self._start_time_by_run_id.pop(run_id, None)
            if start is not None:
                duration_s = time.perf_counter() - start
        duration_part = f" duration_s={duration_s:.3f}" if duration_s is not None else ""
        print(
            f"\n[tool:end] run_id={run_id} parent_run_id={parent_run_id}{duration_part}\n{_format_message_content(output)}\n"
        )

    def on_tool_error(self, error, run_id=None, parent_run_id=None, **kwargs):
        print(f"\n[tool:error] run_id={run_id} parent_run_id={parent_run_id}\n{error}\n")


class TokenUsageTracker(BaseCallbackHandler):
    def __init__(self):
        self.totals: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.by_model: dict[str, dict[str, int]] = {}
        self.calls: list[dict[str, Any]] = []
        self._llm_start_by_run_id: dict[str, dict[str, Any]] = {}

    def _add_usage(self, usage: dict[str, Any], model_name: Optional[str]) -> dict[str, int]:
        def _to_int(value: Any) -> int:
            try:
                return int(value or 0)
            except Exception:
                return 0

        prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion_tokens = _to_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        total_tokens = _to_int(usage.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        self.totals["prompt_tokens"] += prompt_tokens
        self.totals["completion_tokens"] += completion_tokens
        self.totals["total_tokens"] += total_tokens

        key = model_name or "unknown"
        bucket = self.by_model.setdefault(
            key,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["total_tokens"] += total_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def on_llm_start(self, serialized, prompts, run_id=None, parent_run_id=None, **kwargs):
        if run_id is None:
            return
        llm_name = None
        try:
            llm_name = (serialized or {}).get("name")
        except Exception:
            llm_name = None
        self._llm_start_by_run_id[str(run_id)] = {
            "started_at": time.time(),
            "llm": llm_name,
            "n_prompts": len(prompts) if isinstance(prompts, list) else None,
            "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
            "tags": kwargs.get("tags") if isinstance(kwargs.get("tags"), list) else None,
            "metadata": kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else None,
        }

    def on_llm_end(self, response, run_id=None, **kwargs):
        usage = None
        model_name = None
        try:
            model_name = getattr(response, "llm_output", None) and response.llm_output.get("model_name")
        except Exception:
            model_name = None

        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        except Exception:
            usage = None

        if not isinstance(usage, dict):
            try:
                generations = getattr(response, "generations", None) or []
                if generations and generations[0] and generations[0][0]:
                    generation_info = getattr(generations[0][0], "generation_info", None) or {}
                    usage = generation_info.get("token_usage") or generation_info.get("usage")
                    if model_name is None:
                        model_name = generation_info.get("model_name") or generation_info.get("model")
            except Exception:
                usage = None

        if not isinstance(usage, dict):
            return

        normalized = self._add_usage(usage, model_name)
        meta = self._llm_start_by_run_id.pop(str(run_id), {}) if run_id is not None else {}
        started_at = meta.get("started_at")
        ended_at = time.time()
        duration_s = None
        if isinstance(started_at, (int, float)):
            duration_s = round(ended_at - started_at, 6)
        self.calls.append(
            {
                "run_id": str(run_id) if run_id is not None else None,
                "parent_run_id": meta.get("parent_run_id"),
                "llm": meta.get("llm"),
                "model": model_name or "unknown",
                "prompt_tokens": normalized["prompt_tokens"],
                "completion_tokens": normalized["completion_tokens"],
                "total_tokens": normalized["total_tokens"],
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_s": duration_s,
                "n_prompts": meta.get("n_prompts"),
                "tags": meta.get("tags"),
                "metadata": meta.get("metadata"),
            }
        )

    def write_report(self, reports_dir: str, thread_id: str) -> Path:
        report_path = Path(reports_dir) / f"token_usage-{thread_id}.json"
        report_path.write_text(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "totals": self.totals,
                    "by_model": self.by_model,
                    "calls": self.calls,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return report_path
