import os
from unittest import TestCase
from unittest.mock import patch

from deepagents.backends import LocalShellBackend

from agent_core.config import get_project_paths
from agent_core.runtime import create_supervisor_agent


class TestRuntimePermissions(TestCase):
    def test_local_shell_backend_disables_permissions(self):
        paths = get_project_paths(os.getcwd())
        backend = LocalShellBackend(root_dir=paths.project_dir, env=os.environ.copy(), virtual_mode=True)
        with patch("agent_core.runtime.create_deep_agent") as p:
            create_supervisor_agent(
                supervisor_llm=object(),
                subagent_llm=object(),
                paths=paths,
                supervisor_prompt="x",
                backend=backend,
            )
            kwargs = p.call_args.kwargs
            self.assertNotIn("permissions", kwargs)
