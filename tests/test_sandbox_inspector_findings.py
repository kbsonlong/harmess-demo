import unittest
from unittest.mock import Mock, patch

from sandbox_inspector.inspector import Inspector, InspectorConfig


class TestInspectorRun(unittest.TestCase):
    @patch("sandbox_inspector.inspector._load_config", autospec=True)
    @patch("sandbox_inspector.inspector.client.VersionApi", autospec=True)
    @patch("sandbox_inspector.inspector.client.AuthorizationV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.StorageV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.BatchV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.AppsV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.CoreV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.ApiClient", autospec=True)
    def test_run_summarizes_conclusion(
        self,
        api_client_mock,
        core_mock,
        apps_mock,
        batch_mock,
        storage_mock,
        authz_mock,
        version_mock,
        load_cfg_mock,
    ):
        api_client = Mock()
        api_client_mock.return_value = api_client
        api_client.sanitize_for_serialization.side_effect = lambda x: x
        version = Mock()
        version.get_code.return_value = {"gitVersion": "v1.30.0"}
        version_mock.return_value = version

        ins = Inspector(InspectorConfig(max_findings=10))
        ins._probe_permissions = Mock(return_value={"checks": [], "missing": []})
        ins._check_nodes = Mock(return_value=[{"id": "F-x", "severity": "P0"}])
        ins._check_kube_system = Mock(return_value=[])
        ins._check_workloads = Mock(return_value=[])
        ins._check_pods = Mock(return_value=[])
        ins._check_storage = Mock(return_value=[])
        ins._check_quota_limits = Mock(return_value=[])

        out = ins.run()
        self.assertEqual(out["summary"]["conclusion"], "outage")
        self.assertEqual(out["summary"]["counts"]["P0"], 1)

    @patch("sandbox_inspector.inspector._load_config", autospec=True)
    @patch("sandbox_inspector.inspector.client.VersionApi", autospec=True)
    @patch("sandbox_inspector.inspector.client.AuthorizationV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.StorageV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.BatchV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.AppsV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.CoreV1Api", autospec=True)
    @patch("sandbox_inspector.inspector.client.ApiClient", autospec=True)
    def test_run_caps_findings(
        self,
        api_client_mock,
        core_mock,
        apps_mock,
        batch_mock,
        storage_mock,
        authz_mock,
        version_mock,
        load_cfg_mock,
    ):
        api_client = Mock()
        api_client_mock.return_value = api_client
        api_client.sanitize_for_serialization.side_effect = lambda x: x
        version = Mock()
        version.get_code.return_value = {"gitVersion": "v1.30.0"}
        version_mock.return_value = version

        ins = Inspector(InspectorConfig(max_findings=1))
        ins._probe_permissions = Mock(return_value={"checks": [], "missing": []})
        ins._check_nodes = Mock(return_value=[{"id": "F-1", "severity": "P1"}, {"id": "F-2", "severity": "P1"}])
        ins._check_kube_system = Mock(return_value=[])
        ins._check_workloads = Mock(return_value=[])
        ins._check_pods = Mock(return_value=[])
        ins._check_storage = Mock(return_value=[])
        ins._check_quota_limits = Mock(return_value=[])

        out = ins.run()
        self.assertEqual(len(out["findings"]), 1)
        self.assertTrue(out["stats"]["truncated"].get("max_findings_reached"))


if __name__ == "__main__":
    unittest.main()

