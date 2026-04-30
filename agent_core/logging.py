import json
import time
from typing import Any

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

