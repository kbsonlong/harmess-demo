import json
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Optional


def generate_thread_id(prefix: str = "release") -> str:
    return f"{prefix}_{int(time.time())}_{random.randint(0, 100000)}"


def _run_capture(cmd: list[str], *, cwd: Optional[str] = None) -> dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "cmd": cmd,
            "cwd": cwd,
            "exit_code": p.returncode,
            "stdout": (p.stdout or "").strip(),
            "stderr": (p.stderr or "").strip(),
        }
    except Exception as e:
        return {
            "cmd": cmd,
            "cwd": cwd,
            "exit_code": 127,
            "stdout": "",
            "stderr": str(e),
        }


def collect_git_info(project_dir: str) -> dict[str, Any]:
    head = _run_capture(["git", "rev-parse", "HEAD"], cwd=project_dir)
    status = _run_capture(["git", "status", "--porcelain"], cwd=project_dir)
    commit = head["stdout"] if head.get("exit_code") == 0 else None
    dirty = None
    if status.get("exit_code") == 0:
        dirty = bool(status.get("stdout"))
    return {
        "commit": commit,
        "dirty": dirty,
        "errors": [x for x in [head.get("stderr"), status.get("stderr")] if x],
    }


def build_release_metadata(
    *,
    thread_id: str,
    project_dir: str,
    kind_cluster_name: Optional[str],
    kube_context: Optional[str],
    kubernetes_version: Optional[str],
    argocd: Optional[dict[str, Any]],
    demo_app: Optional[dict[str, Any]],
    manifests: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "thread_id": thread_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": {
            "dir": project_dir,
            "git": collect_git_info(project_dir),
        },
        "cluster": {
            "kind_cluster_name": kind_cluster_name,
            "kube_context": kube_context,
            "kubernetes_version": kubernetes_version,
        },
        "components": {
            "argocd": argocd,
            "demo_app": demo_app,
        },
        "manifests": manifests,
    }


def write_release_metadata(*, reports_dir: str, thread_id: str, payload: dict[str, Any]) -> Path:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"release_metadata-{thread_id}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
