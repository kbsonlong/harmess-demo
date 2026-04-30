import os
from dataclasses import dataclass

from langchain_openai import ChatOpenAI


@dataclass(frozen=True)
class ProjectPaths:
    project_dir: str
    reports_dir: str
    skills_dir: str


def get_project_paths(project_dir: str) -> ProjectPaths:
    return ProjectPaths(
        project_dir=project_dir,
        reports_dir=os.path.join(project_dir, "reports"),
        skills_dir=os.path.join(project_dir, "skills"),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def create_llm_from_env() -> ChatOpenAI:
    return ChatOpenAI(
        api_key=_require_env("API_KEY"),
        model=_require_env("MODEL"),
        base_url=_require_env("API_BASE"),
    )

