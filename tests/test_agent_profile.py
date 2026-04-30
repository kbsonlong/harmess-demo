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
            (base / "prompts" / "p.md").write_text("plat", encoding="utf-8")
            profile_path = base / "profiles" / "x.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "name": "x",
                        "supervisor_prompt_path": "../prompts/s.md",
                        "platform_expert_prompt_path": "../prompts/p.md",
                        "initial_user_message": "hi",
                        "recursion_limit": 7,
                        "include_subagents": ["infra_expert", "fault_expert"],
                    }
                ),
                encoding="utf-8",
            )
            p = load_profile_from_path(profile_path)
            self.assertEqual(p.name, "x")
            self.assertEqual(p.supervisor_prompt, "hello")
            self.assertEqual(p.platform_expert_prompt, "plat")
            self.assertEqual(p.initial_user_message, "hi")
            self.assertEqual(p.recursion_limit, 7)
            self.assertEqual(p.include_subagents, ["infra_expert", "fault_expert"])
