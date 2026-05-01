import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from agent_core.logging import TokenUsageTracker


class TestTokenUsageTracker(TestCase):
    def test_collects_usage_and_writes_report(self):
        tracker = TokenUsageTracker()
        tracker.on_llm_start({"name": "ChatOpenAI"}, ["prompt"], run_id="run-1", parent_run_id="parent-1")
        tracker.on_llm_end(
            SimpleNamespace(
                llm_output={
                    "model_name": "gpt-test",
                    "token_usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                }
            ),
            run_id="run-1",
        )

        self.assertEqual(tracker.totals["prompt_tokens"], 11)
        self.assertEqual(tracker.totals["completion_tokens"], 7)
        self.assertEqual(tracker.totals["total_tokens"], 18)
        self.assertEqual(tracker.by_model["gpt-test"]["total_tokens"], 18)
        self.assertEqual(len(tracker.calls), 1)

        with tempfile.TemporaryDirectory() as d:
            path = tracker.write_report(d, "thread-1")
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(data["thread_id"], "thread-1")
            self.assertEqual(data["totals"]["total_tokens"], 18)
            self.assertEqual(data["calls"][0]["model"], "gpt-test")
