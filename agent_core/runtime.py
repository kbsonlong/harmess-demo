import os
import random
from typing import Any, Optional

from deepagents import FilesystemPermission, SubAgent, create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from k8s_sandbox import exec_in_sandbox

from .victorialogs import victorialogs_query
from .config import ProjectPaths
from .logging import TokenUsageTracker, ToolEventPrinter


def _inject_admin_workflow(prompt: Optional[str], workflow_md: Optional[str]) -> Optional[str]:
    if not prompt:
        return prompt
    workflow = (workflow_md or "").strip()
    if "{{ADMIN_WORKFLOW}}" in prompt:
        return prompt.replace("{{ADMIN_WORKFLOW}}", workflow)
    if not workflow:
        return prompt
    return f"{prompt}\n\n{workflow}"


def create_subagents(
    llm,
    *,
    planner_prompt: Optional[str] = None,
    executor_prompt: Optional[str] = None,
    validator_prompt: Optional[str] = None,
    workflow_md: Optional[str] = None,
    include_subagents: Optional[list[str]] = None,
) -> list[SubAgent]:
    planner = SubAgent(
        name="planner",
        description="规划专家。负责解析用户意图与用户自定义工作流程，将任务拆分为可执行的最小证据链任务清单。",
        model=llm,
        tools=[],
        system_prompt=_inject_admin_workflow(planner_prompt, workflow_md)
        or "你是 Planner。只输出任务拆分与路径选择，禁止执行命令、禁止调用工具。",
    )

    executor = SubAgent(
        name="executor",
        description="执行专家。负责按任务清单采集证据、定位异常并给出可执行修复建议与验证点（仅建议，不做写操作）。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=executor_prompt
        or "你是 Executor。按任务清单执行取证与诊断，编号证据并给出修复建议与验证点。严禁执行写操作。",
    )

    validator = SubAgent(
        name="validator",
        description="校验专家。负责审核证据链完整性与准出标准，指出缺口并给出最小补采建议。",
        model=llm,
        tools=[],
        system_prompt=validator_prompt
        or "你是 Validator。只做证据链审计与准出判断，禁止执行命令、禁止调用工具。",
    )

    by_name = {
        "planner": planner,
        "executor": executor,
        "validator": validator,
    }

    include = include_subagents or ["planner", "executor", "validator"]
    result: list[SubAgent] = []
    for name in include:
        agent = by_name.get(name)
        if agent is not None:
            result.append(agent)
    return result


def create_supervisor_agent(
    *,
    llm,
    paths: ProjectPaths,
    supervisor_prompt: str,
    planner_prompt: Optional[str] = None,
    executor_prompt: Optional[str] = None,
    validator_prompt: Optional[str] = None,
    workflow_md: Optional[str] = None,
    include_subagents: Optional[list[str]] = None,
    backend: Optional[Any] = None,
    checkpointer: Optional[MemorySaver] = None,
):
    backend = backend or FilesystemBackend(root_dir=paths.project_dir, virtual_mode=True)
    checkpointer = checkpointer or MemorySaver()

    subagents = create_subagents(
        llm,
        planner_prompt=planner_prompt,
        executor_prompt=executor_prompt,
        validator_prompt=validator_prompt,
        workflow_md=workflow_md,
        include_subagents=include_subagents,
    )
    permissions = None
    backend_supports_execute = hasattr(backend, "execute")
    if isinstance(backend, FilesystemBackend) and not backend_supports_execute:
        permissions = [
            FilesystemPermission(
                operations=["read", "write"],
                paths=[os.path.join(paths.reports_dir, "**")],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read"],
                paths=[os.path.join(paths.skills_dir, "**")],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ]

    kwargs: dict[str, Any] = {
        "model": llm,
        "tools": [exec_in_sandbox, victorialogs_query],
        "system_prompt": supervisor_prompt,
        "subagents": subagents,
        "skills": [paths.skills_dir],
        "interrupt_on": {"write_file": False, "read_file": False, "edit_file": False},
        "backend": backend,
        "checkpointer": checkpointer,
    }
    if permissions is not None:
        kwargs["permissions"] = permissions

    return create_deep_agent(
        **kwargs,
    )


def run_supervisor(
    *,
    agent,
    initial_user_message: str,
    thread_id: Optional[str] = None,
    recursion_limit: int = 150,
    reports_dir: Optional[str] = None,
) -> str:
    thread_id = thread_id or f"k8s_multi_agent_{random.randint(0, 100000)}"
    token_tracker = TokenUsageTracker()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
        "callbacks": [ToolEventPrinter(), token_tracker],
    }
    initial_state = {"messages": [{"role": "user", "content": initial_user_message}]}
    for chunk in agent.stream(initial_state, config):
        if "agent" in chunk:
            msg = chunk["agent"]["messages"][-1]
            print(f"\n--- [Supervisor]: {msg.content}")
        elif "call_subagent" in chunk:
            print(f"\n--- [调度专家]: {chunk['call_subagent']}")
    if reports_dir:
        token_usage_path = token_tracker.write_report(reports_dir, thread_id)
        print(
            f"\n[Token统计] total={token_tracker.totals.get('total_tokens', 0)} "
            f"(prompt={token_tracker.totals.get('prompt_tokens', 0)}, "
            f"completion={token_tracker.totals.get('completion_tokens', 0)})"
        )
        print(f"[Token统计] 明细已保存: {token_usage_path}")
    return thread_id
