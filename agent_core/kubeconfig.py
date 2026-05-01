import os
from pathlib import Path
from typing import Optional


def default_demo_kubeconfig_path(project_dir: Optional[str] = None) -> str:
    base = Path(project_dir) if project_dir else Path(__file__).resolve().parent.parent
    return str((base / ".demo" / "kubeconfig").resolve())


def ensure_kubeconfig_env_default(project_dir: Optional[str] = None) -> str:
    existing = os.environ.get("KUBECONFIG")
    if existing:
        return existing

    kubeconfig = default_demo_kubeconfig_path(project_dir)
    Path(kubeconfig).parent.mkdir(parents=True, exist_ok=True)
    os.environ["KUBECONFIG"] = kubeconfig
    return kubeconfig
