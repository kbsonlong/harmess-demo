import json
import tempfile
from pathlib import Path
from unittest import TestCase

from agent_core.runtime import run_supervisor


class _BoomAgent:
    def __init__(self):
        self.calls = 0

    def stream(self, state, config):
        self.calls += 1
        raise RuntimeError("boom")


class TestRunSupervisorResilience(TestCase):
    def test_run_supervisor_does_not_raise_when_agent_stream_throws(self):
        with tempfile.TemporaryDirectory() as d:
            reports_dir = str(Path(d) / "reports")
            agent = _BoomAgent()
            thread_id = run_supervisor(
                agent=agent,
                initial_user_message="hi",
                thread_id="t_resilience",
                recursion_limit=5,
                reports_dir=reports_dir,
            )
            self.assertEqual(thread_id, "t_resilience")
            p = Path(reports_dir) / "token_usage-t_resilience.json"
            self.assertTrue(p.exists())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data.get("thread_id"), "t_resilience")
            self.assertGreaterEqual(agent.calls, 1)
