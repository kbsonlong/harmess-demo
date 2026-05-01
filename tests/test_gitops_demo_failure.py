import tempfile
from pathlib import Path
from unittest import TestCase

from gitops_demo import _deployment_label_selector, _extract_waiting_reason_hit, _try_load_json, _write_json_report


class TestGitopsDemoFailure(TestCase):
    def test_deployment_label_selector_sorted(self):
        dep = {"spec": {"selector": {"matchLabels": {"b": "2", "a": "1"}}}}
        self.assertEqual(_deployment_label_selector(dep), "a=1,b=2")

    def test_extract_waiting_reason_hit(self):
        pod = {
            "metadata": {"namespace": "demo-app", "name": "p1"},
            "status": {
                "containerStatuses": [
                    {
                        "name": "c1",
                        "restartCount": 3,
                        "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "Back-off restarting failed container"}},
                    }
                ]
            },
        }
        hit = _extract_waiting_reason_hit(pod, {"CrashLoopBackOff"})
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["namespace"], "demo-app")
        self.assertEqual(hit["pod"], "p1")
        self.assertEqual(hit["container"], "c1")
        self.assertEqual(hit["reason"], "CrashLoopBackOff")
        self.assertEqual(hit["restart_count"], 3)

    def test_write_and_load_json_report(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"schema_version": "1", "release_id": "t1"}
            out = _write_json_report(reports_dir=d, filename="x.json", payload=payload)
            loaded = _try_load_json(Path(out))
            self.assertEqual(loaded, payload)

