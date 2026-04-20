import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import k8s_sandbox


class TestSandboxManifests(unittest.TestCase):
    def test_manifest_security_defaults(self):
        data = k8s_sandbox.render_sandbox_manifests(
            namespace="default",
            image="busybox:1.36",
            ttl_seconds=900,
            rbac_profile="readonly",
            allow_exec=True,
            read_only_root_filesystem=True,
            sandbox_id="abc123",
        )
        manifest = data["manifest"]
        self.assertEqual(manifest["kind"], "List")
        items = manifest["items"]
        pod = [x for x in items if x["kind"] == "Pod"][0]

        spec = pod["spec"]
        self.assertEqual(spec.get("hostNetwork", False), False)
        self.assertEqual(spec.get("hostPID", False), False)
        self.assertEqual(spec.get("hostIPC", False), False)

        psc = spec["securityContext"]
        self.assertTrue(psc["runAsNonRoot"])
        self.assertEqual(psc["runAsUser"], 1000)
        self.assertEqual(psc["seccompProfile"]["type"], "RuntimeDefault")

        csc = spec["containers"][0]["securityContext"]
        self.assertFalse(csc["allowPrivilegeEscalation"])
        self.assertTrue(csc["readOnlyRootFilesystem"])
        self.assertEqual(csc["capabilities"]["drop"], ["ALL"])

    def test_role_disallows_secrets(self):
        data = k8s_sandbox.render_sandbox_manifests(namespace="ns", rbac_profile="readonly", sandbox_id="abc123")
        items = data["manifest"]["items"]
        role = [x for x in items if x["kind"] == "Role"][0]
        role_json = json.dumps(role)
        self.assertNotIn("secrets", role_json)


class TestKubernetesSDKCalls(unittest.TestCase):
    @patch("k8s_sandbox._wait_pod_ready")
    @patch("k8s_sandbox._get_apis")
    def test_create_sandbox_creates_resources_and_waits(self, get_apis_mock, wait_mock):
        core = Mock()
        rbac = Mock()
        api_client = Mock()
        get_apis_mock.return_value = (core, rbac, api_client)
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[])

        data = k8s_sandbox.create_sandbox(
            namespace="default",
            ttl_seconds=120,
            rbac_profile="readonly",
            dry_run=False,
            wait_ready=True,
            apply_rbac=True,
        )

        core.create_namespaced_service_account.assert_called_once()
        core.create_namespaced_pod.assert_called_once()
        rbac.create_namespaced_role.assert_called_once()
        rbac.create_namespaced_role_binding.assert_called_once()
        wait_mock.assert_called_once()
        self.assertEqual(data["namespace"], "default")

    @patch("k8s_sandbox._wait_pod_ready")
    @patch("k8s_sandbox._get_apis")
    def test_create_sandbox_reuses_existing_pod(self, get_apis_mock, wait_mock):
        core = Mock()
        rbac = Mock()
        api_client = Mock()
        get_apis_mock.return_value = (core, rbac, api_client)

        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="p", creation_timestamp=SimpleNamespace(timestamp=lambda: 1.0)),
            status=SimpleNamespace(phase="Running", conditions=[SimpleNamespace(type="Ready", status="True")]),
            spec=SimpleNamespace(containers=[SimpleNamespace(name="sandbox")], service_account_name="k8s-sandbox-sa"),
        )
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

        data = k8s_sandbox.create_sandbox(namespace="default", dry_run=False, wait_ready=True)

        core.create_namespaced_service_account.assert_not_called()
        core.create_namespaced_pod.assert_not_called()
        rbac.create_namespaced_role.assert_not_called()
        rbac.create_namespaced_role_binding.assert_not_called()
        wait_mock.assert_not_called()
        self.assertEqual(data["pod_name"], "p")
        self.assertTrue(data["reused"])

    @patch("k8s_sandbox._get_apis")
    @patch("k8s_sandbox.stream")
    def test_exec_in_sandbox_streams_stdout_stderr(self, stream_mock, get_apis_mock):
        core = Mock()
        get_apis_mock.return_value = (core, Mock(), Mock())

        def build_resp():
            resp = SimpleNamespace()
            state = {"open": True}

            def is_open():
                return state["open"]

            def update(timeout=1):
                state["open"] = False

            resp.is_open = is_open
            resp.update = update
            resp.peek_stdout = lambda: True
            resp.read_stdout = lambda: "ok"
            resp.peek_stderr = lambda: False
            resp.read_stderr = lambda: ""
            resp.close = lambda: None
            resp.channel = {}
            return resp

        stream_mock.side_effect = lambda *args, **kwargs: build_resp()

        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="p", creation_timestamp=SimpleNamespace(timestamp=lambda: 1.0)),
            status=SimpleNamespace(phase="Running", conditions=[SimpleNamespace(type="Ready", status="True")]),
            spec=SimpleNamespace(containers=[SimpleNamespace(name="sandbox")]),
        )
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

        res = k8s_sandbox.exec_in_sandbox(
            namespace="default",
            label_selector="app=k8s-sandbox,sandbox-id=abc123",
            command=["echo", "hi"],
            timeout_seconds=5,
        )
        self.assertEqual(res["stdout"], "ok")
        self.assertEqual(res["stderr"], "")
        res2 = k8s_sandbox.exec_in_sandbox(
            namespace="default",
            label_selector="app=k8s-sandbox,sandbox-id=abc123",
            commands='["echo", "hi"]',
            timeout_seconds=5,
        )
        self.assertEqual(res2["stdout"], "ok")

    def test_exec_rejects_invalid_command(self):
        with self.assertRaises(ValueError):
            k8s_sandbox.exec_in_sandbox(namespace="default", pod_name="p", command=[])
        with self.assertRaises(ValueError):
            k8s_sandbox.exec_in_sandbox(namespace="default", pod_name="p", command=None)

    @patch("k8s_sandbox._get_apis")
    def test_cleanup_deletes_resources(self, get_apis_mock):
        core = Mock()
        rbac = Mock()
        get_apis_mock.return_value = (core, rbac, Mock())

        k8s_sandbox.cleanup_sandbox(
            namespace="ns",
            pod_name="p",
            service_account_name="sa",
            role_name="role",
            role_binding_name="rb",
            cluster_role_name="cr",
            cluster_role_binding_name="crb",
        )

        core.delete_namespaced_pod.assert_called_once()
        core.delete_namespaced_service_account.assert_called_once()
        rbac.delete_namespaced_role.assert_called_once()
        rbac.delete_namespaced_role_binding.assert_called_once()
        rbac.delete_cluster_role_binding.assert_called_once()
        rbac.delete_cluster_role.assert_called_once()


if __name__ == "__main__":
    unittest.main()
