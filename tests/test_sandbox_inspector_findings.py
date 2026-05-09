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

    def test_check_pods_includes_crashloop_running_pod(self):
        ins = Inspector.__new__(Inspector)
        ins.config = InspectorConfig(max_findings=10)
        ins.api_client = Mock()
        ins.api_client.sanitize_for_serialization.side_effect = lambda x: x
        ins.core = Mock()

        healthy_running = {
            "metadata": {"namespace": "default", "name": "ok-pod"},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"ready": True, "restartCount": 0, "state": {"running": {}}}],
            },
        }
        crashloop_running = {
            "metadata": {"namespace": "sandbox-demo", "name": "bad-crashloop"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "ready": False,
                        "restartCount": 5,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    }
                ],
            },
        }
        permissions = {"checks": [{"group": "", "resource": "pods", "verb": "list", "allowed": True}]}
        stats = {"scanned": {}, "truncated": {}}

        with patch("sandbox_inspector.inspector._pagination_loop", return_value=[healthy_running, crashloop_running]):
            findings = ins._check_pods(permissions, stats)

        self.assertEqual(stats["scanned"]["abnormal_pods"], 2)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["title"], "异常 Pod：CrashLoopBackOff")
        self.assertEqual(findings[0]["severity"], "P1")
        self.assertEqual(findings[0]["focus_refs"][0]["name"], "bad-crashloop")

    def test_check_kube_system_pods_include_namespace(self):
        ins = Inspector.__new__(Inspector)
        ins.config = InspectorConfig(max_findings=10)
        ins.api_client = Mock()
        ins.api_client.sanitize_for_serialization.side_effect = lambda x: x
        ins.core = Mock()

        permissions = {"checks": [{"group": "", "resource": "pods", "verb": "list", "allowed": True}]}
        stats = {"scanned": {}, "truncated": {}}
        pod = {"metadata": {"name": "coredns-0"}, "status": {"phase": "Pending"}}

        with patch("sandbox_inspector.inspector._pagination_loop", return_value=[pod]):
            findings = ins._check_kube_system(permissions, stats)

        self.assertEqual(findings[0]["evidence"][0]["ref"]["pods"][0]["namespace"], "kube-system")
        self.assertEqual(findings[0]["focus_refs"][0]["namespace"], "kube-system")

    def test_check_deployments_focus_ref_has_namespace_from_object(self):
        ins = Inspector.__new__(Inspector)
        ins.config = InspectorConfig(max_findings=10)
        ins.api_client = Mock()
        ins.apps = Mock()

        dep = Mock()
        dep.metadata = Mock(namespace="ns-a", name="dep-a")
        ins.api_client.sanitize_for_serialization.side_effect = lambda x: {
            "metadata": {"name": "dep-a"},
            "spec": {"replicas": 1},
            "status": {"availableReplicas": 0},
        }
        stats = {"scanned": {}, "truncated": {}}

        with patch("sandbox_inspector.inspector._pagination_loop", return_value=[dep]):
            findings = ins._check_deployments(stats)

        self.assertEqual(findings[0]["evidence"][0]["ref"]["deployments"][0]["namespace"], "ns-a")
        self.assertEqual(findings[0]["focus_refs"][0]["namespace"], "ns-a")

    def test_check_pods_focus_ref_has_namespace_from_object(self):
        ins = Inspector.__new__(Inspector)
        ins.config = InspectorConfig(max_findings=10)
        ins.api_client = Mock()
        ins.core = Mock()

        pod = Mock()
        pod.metadata = Mock(namespace="ns-b", name="bad-crashloop")
        ins.api_client.sanitize_for_serialization.side_effect = lambda x: {
            "metadata": {"name": "bad-crashloop"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"ready": False, "restartCount": 3, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
                ],
            },
        }
        permissions = {"checks": [{"group": "", "resource": "pods", "verb": "list", "allowed": True}]}
        stats = {"scanned": {}, "truncated": {}}

        with patch("sandbox_inspector.inspector._pagination_loop", return_value=[pod]):
            findings = ins._check_pods(permissions, stats)

        self.assertEqual(findings[0]["evidence"][0]["ref"]["pods"][0]["namespace"], "ns-b")
        self.assertEqual(findings[0]["focus_refs"][0]["namespace"], "ns-b")


if __name__ == "__main__":
    unittest.main()
