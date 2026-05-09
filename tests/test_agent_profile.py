import json
import tempfile
from pathlib import Path
from unittest import TestCase

from agent_core.config import get_project_paths
from agent_core.profile import load_profile, load_profile_from_path


class TestAgentProfile(TestCase):
    def test_load_profile_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            paths = get_project_paths(d)
            p = load_profile(paths, "nope")
            self.assertEqual(p.name, "nope")
            self.assertIsNone(p.supervisor_prompt)

    def test_load_profile_from_path_resolves_relative_prompt_paths(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "prompts").mkdir(parents=True, exist_ok=True)
            (base / "profiles").mkdir(parents=True, exist_ok=True)
            (base / "prompts" / "s.md").write_text("hello", encoding="utf-8")
            (base / "prompts" / "planner.md").write_text("plan", encoding="utf-8")
            (base / "prompts" / "executor.md").write_text("exec", encoding="utf-8")
            (base / "prompts" / "validator.md").write_text("val", encoding="utf-8")
            (base / "prompts" / "wf.md").write_text("workflow", encoding="utf-8")
            profile_path = base / "profiles" / "x.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "name": "x",
                        "supervisor_prompt_path": "../prompts/s.md",
                        "planner_prompt_path": "../prompts/planner.md",
                        "executor_prompt_path": "../prompts/executor.md",
                        "validator_prompt_path": "../prompts/validator.md",
                        "workflow_md_path": "../prompts/wf.md",
                        "initial_user_message": "hi",
                        "recursion_limit": 7,
                        "include_subagents": ["planner", "executor", "validator"],
                    }
                ),
                encoding="utf-8",
            )
            p = load_profile_from_path(profile_path)
            self.assertEqual(p.name, "x")
            self.assertEqual(p.supervisor_prompt, "hello")
            self.assertEqual(p.planner_prompt, "plan")
            self.assertEqual(p.executor_prompt, "exec")
            self.assertEqual(p.validator_prompt, "val")
            self.assertEqual(p.workflow_md, "workflow")
            self.assertEqual(p.initial_user_message, "hi")
            self.assertEqual(p.recursion_limit, 7)
            self.assertEqual(p.include_subagents, ["planner", "executor", "validator"])
