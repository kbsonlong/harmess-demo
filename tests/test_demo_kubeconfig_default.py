import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import kind_demo
from agent_core.kubeconfig import ensure_kubeconfig_env_default


class TestDemoKubeconfigDefault(TestCase):
    def test_ensure_sets_project_demo_kubeconfig_when_unset(self):
        repo_root = Path(__file__).resolve().parents[1]
        expected = str((repo_root / ".demo" / "kubeconfig").resolve())
        with patch.dict(os.environ, {}, clear=True):
            got = ensure_kubeconfig_env_default()
            self.assertEqual(got, expected)
            self.assertEqual(os.environ.get("KUBECONFIG"), expected)

    def test_ensure_does_not_override_existing_kubeconfig(self):
        with patch.dict(os.environ, {"KUBECONFIG": "/tmp/custom-kubeconfig"}, clear=True):
            got = ensure_kubeconfig_env_default()
            self.assertEqual(got, "/tmp/custom-kubeconfig")
            self.assertEqual(os.environ.get("KUBECONFIG"), "/tmp/custom-kubeconfig")

    def test_kind_demo_load_kube_config_uses_demo_path(self):
        repo_root = Path(__file__).resolve().parents[1]
        expected = str((repo_root / ".demo" / "kubeconfig").resolve())
        with patch.dict(os.environ, {}, clear=True), patch.object(kind_demo.config, "load_kube_config") as m:
            kind_demo._load_kube_config()
            m.assert_called_once_with(config_file=expected)
