import json
import tempfile
from pathlib import Path
from unittest import TestCase

from agent_core.runtime import run_supervisor


class TestRunSupervisorResilience(TestCase):
    def test_run_supervisor_does_not_raise_on_stream_error_and_writes_token_usage(self):
        class BadAgent:
            def stream(self, initial_state, config):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as d:
            thread_id = run_supervisor(
                agent=BadAgent(),
                initial_user_message="x",
                thread_id="t1",
                reports_dir=d,
            )
            self.assertEqual(thread_id, "t1")
            p = Path(d) / "token_usage-t1.json"
            self.assertTrue(p.exists())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["thread_id"], "t1")
