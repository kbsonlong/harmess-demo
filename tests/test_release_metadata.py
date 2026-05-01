import json
import tempfile
from pathlib import Path
from unittest import TestCase

from release_metadata import build_release_metadata, write_release_metadata


class TestReleaseMetadata(TestCase):
    def test_write_release_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            payload = build_release_metadata(
                thread_id="t1",
                project_dir=d,
                kind_cluster_name="demo04",
                kube_context="kind-demo04",
                kubernetes_version="v1.30.0",
                argocd={"namespace": "argocd"},
                demo_app={"namespace": "demo-app"},
                manifests=["x", "y"],
            )
            out = write_release_metadata(reports_dir=d, thread_id="t1", payload=payload)
            self.assertTrue(out.exists())

            loaded = json.loads(Path(out).read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema_version"], "1")
            self.assertEqual(loaded["thread_id"], "t1")
            self.assertEqual(loaded["cluster"]["kind_cluster_name"], "demo04")
            self.assertEqual(loaded["components"]["argocd"]["namespace"], "argocd")
