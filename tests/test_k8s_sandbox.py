import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import k8s_sandbox


class TestKubernetesSDKCalls(unittest.TestCase):
    @patch("k8s_sandbox._get_core_v1")
    @patch("k8s_sandbox.stream")
    def test_exec_in_sandbox_streams_stdout_stderr(self, stream_mock, get_core_mock):
        core = Mock()
        get_core_mock.return_value = core

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
        res = k8s_sandbox.exec_in_sandbox(namespace="default", pod_name="p", command=[])
        self.assertEqual(res["error"], "invalid_command")
        self.assertIn("command must be a non-empty list of strings", res["stderr"])
        res2 = k8s_sandbox.exec_in_sandbox(namespace="default", pod_name="p", command=None)
        self.assertEqual(res2["error"], "invalid_command")
        self.assertIn("command must be a non-empty list of strings", res2["stderr"])

    def test_exec_rejects_pod_name_and_label_selector_together(self):
        res = k8s_sandbox.exec_in_sandbox(
            namespace="default",
            pod_name="p",
            label_selector="app=k8s-sandbox",
            command=["echo", "hi"],
        )
        self.assertEqual(res["error"], "invalid_arguments")
        self.assertIn("must not provide both pod_name and label_selector", res["stderr"])

    @patch("k8s_sandbox._get_core_v1")
    def test_exec_returns_error_when_no_pod_matched(self, get_core_mock):
        core = Mock()
        get_core_mock.return_value = core
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
        res = k8s_sandbox.exec_in_sandbox(
            namespace="default",
            label_selector="app=k8s-sandbox,sandbox-id=missing",
            command=["echo", "hi"],
        )
        self.assertEqual(res["error"], "no_pod_matched")
        self.assertIn("no_pod_matched", res["stderr"])

    @patch("k8s_sandbox.time.time")
    @patch("k8s_sandbox._get_core_v1")
    @patch("k8s_sandbox.stream")
    def test_exec_returns_error_when_timeout(self, stream_mock, get_core_mock, time_mock):
        core = Mock()
        get_core_mock.return_value = core
        core.read_namespaced_pod.return_value = SimpleNamespace(spec=SimpleNamespace(containers=[SimpleNamespace(name="sandbox")]))

        resp = SimpleNamespace()
        resp.is_open = lambda: True
        resp.update = lambda timeout=1: None
        resp.peek_stdout = lambda: False
        resp.read_stdout = lambda: ""
        resp.peek_stderr = lambda: False
        resp.read_stderr = lambda: ""
        resp.close = lambda: None
        resp.channel = {}
        stream_mock.return_value = resp
        time_mock.side_effect = [0.0, 2.0]

        res = k8s_sandbox.exec_in_sandbox(namespace="default", pod_name="p", command=["echo", "hi"], timeout_seconds=1)
        self.assertEqual(res["error"], "exec_timeout")
        self.assertEqual(res["stderr"], "exec timeout")


if __name__ == "__main__":
    unittest.main()
