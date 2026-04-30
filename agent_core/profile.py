import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ProjectPaths


@dataclass(frozen=True)
class AgentProfile:
    name: str
    supervisor_prompt: Optional[str]
    infra_expert_prompt: Optional[str]
    workload_expert_prompt: Optional[str]
    platform_expert_prompt: Optional[str]
    access_expert_prompt: Optional[str]
    fault_expert_prompt: Optional[str]
    initial_user_message: Optional[str]
    recursion_limit: Optional[int]
    include_subagents: Optional[list[str]]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _resolve_path(base_dir: Path, raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_profile_from_path(profile_path: Path) -> AgentProfile:
    data = json.loads(profile_path.read_text(encoding="utf-8", errors="replace"))
    base_dir = profile_path.parent
    supervisor_path = _resolve_path(base_dir, data.get("supervisor_prompt_path"))
    infra_path = _resolve_path(base_dir, data.get("infra_expert_prompt_path"))
    workload_path = _resolve_path(base_dir, data.get("workload_expert_prompt_path"))
    platform_path = _resolve_path(base_dir, data.get("platform_expert_prompt_path"))
    access_path = _resolve_path(base_dir, data.get("access_expert_prompt_path"))
    fault_path = _resolve_path(base_dir, data.get("fault_expert_prompt_path"))
    return AgentProfile(
        name=str(data.get("name") or profile_path.stem),
        supervisor_prompt=_read_text(supervisor_path) if supervisor_path and supervisor_path.exists() else None,
        infra_expert_prompt=_read_text(infra_path) if infra_path and infra_path.exists() else None,
        workload_expert_prompt=_read_text(workload_path) if workload_path and workload_path.exists() else None,
        platform_expert_prompt=_read_text(platform_path) if platform_path and platform_path.exists() else None,
        access_expert_prompt=_read_text(access_path) if access_path and access_path.exists() else None,
        fault_expert_prompt=_read_text(fault_path) if fault_path and fault_path.exists() else None,
        initial_user_message=(data.get("initial_user_message") or None),
        recursion_limit=(int(data["recursion_limit"]) if "recursion_limit" in data else None),
        include_subagents=(list(data["include_subagents"]) if "include_subagents" in data else None),
    )


def load_profile(paths: ProjectPaths, profile_name: str) -> AgentProfile:
    profile_path = Path(paths.project_dir) / "profiles" / f"{profile_name}.json"
    if not profile_path.exists():
        return AgentProfile(
            name=profile_name,
            supervisor_prompt=None,
            infra_expert_prompt=None,
            workload_expert_prompt=None,
            platform_expert_prompt=None,
            access_expert_prompt=None,
            fault_expert_prompt=None,
            initial_user_message=None,
            recursion_limit=None,
            include_subagents=None,
        )
    return load_profile_from_path(profile_path)
