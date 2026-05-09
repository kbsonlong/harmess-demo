import json
import os
import random
import traceback
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
    subagent_llm,
    *,
    executor_prompt: Optional[str] = None,
    validator_prompt: Optional[str] = None,
    include_subagents: Optional[list[str]] = None,
) -> list[SubAgent]:
    executor = SubAgent(
        name="executor",
        description="执行专家。负责按任务清单采集证据、定位异常并给出可执行修复建议与验证点（仅建议，不做写操作）。",
        model=subagent_llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=executor_prompt
        or "你是 Executor。按任务清单执行取证与诊断，编号证据并给出修复建议与验证点。严禁执行写操作。",
    )

    validator = SubAgent(
        name="validator",
        description="校验专家。负责审核证据链完整性与准出标准，指出缺口并给出最小补采建议。",
        model=subagent_llm,
        tools=[],
        system_prompt=validator_prompt
        or "你是 Validator。只做证据链审计与准出判断，禁止执行命令、禁止调用工具。",
    )

    by_name = {
        "executor": executor,
        "validator": validator,
    }

    include = include_subagents or ["executor", "validator"]
    result: list[SubAgent] = []
    for name in include:
        agent = by_name.get(name)
        if agent is not None:
            result.append(agent)
    return result


def create_supervisor_agent(
    *,
    supervisor_llm,
    subagent_llm,
    paths: ProjectPaths,
    supervisor_prompt: str,
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
        subagent_llm,
        executor_prompt=executor_prompt,
        validator_prompt=validator_prompt,
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
        "model": supervisor_llm,
        "tools": [exec_in_sandbox, victorialogs_query],
        "system_prompt": _inject_admin_workflow(supervisor_prompt, workflow_md),
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
    def _pending_todos() -> list[dict[str, Any]]:
        if not reports_dir:
            return []
        path = os.path.join(reports_dir, "todos.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.loads(f.read())
        except Exception:
            return []
        items = raw.get("todos") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        pending: list[dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            status = t.get("status")
            if status != "completed":
                pending.append(t)
        return pending

    def _persist_exception(exc: BaseException, *, where: str, round_i: int) -> None:
        if not reports_dir:
            return
        try:
            os.makedirs(reports_dir, exist_ok=True)
        except Exception:
            return
        path = os.path.join(reports_dir, f"runtime_exception-{thread_id}-r{round_i}.txt")
        try:
            payload = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"where={where}\n")
                f.write(payload)
        except Exception:
            return

    thread_id = thread_id or f"k8s_multi_agent_{random.randint(0, 100000)}"
    token_tracker = TokenUsageTracker()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
        "callbacks": [ToolEventPrinter(), token_tracker],
    }
    for round_i, user_msg in enumerate(
        [
            initial_user_message,
            "检测到 TODO 未全部完成。请继续严格按 Planner→Executor→Validator 执行未完成任务，更新 todos.json，并在完成后生成并落盘最终报告。",
            "仍存在未完成 TODO。请继续执行剩余任务，直到 todos.json 全部 completed 或者 skipped 且报告已落盘。",
        ],
        start=1,
    ):
        initial_state = {"messages": [{"role": "user", "content": user_msg}]}
        try:
            for chunk in agent.stream(initial_state, config):
                if "agent" in chunk:
                    msg = chunk["agent"]["messages"][-1]
                    print(f"\n--- [Supervisor]: {msg.content}")
                elif "call_subagent" in chunk:
                    print(f"\n--- [调度专家]: {chunk['call_subagent']}")
        except Exception as e:
            print(f"\n[run_supervisor] 捕获异常（不中断主进程）: {type(e).__name__}: {e}")
            _persist_exception(e, where="agent.stream", round_i=round_i)
        if not _pending_todos():
            break
        if round_i >= 3:
            break
    if reports_dir:
        try:
            token_usage_path = token_tracker.write_report(reports_dir, thread_id)
            print(
                f"\n[Token统计] total={token_tracker.totals.get('total_tokens', 0)} "
                f"(prompt={token_tracker.totals.get('prompt_tokens', 0)}, "
                f"completion={token_tracker.totals.get('completion_tokens', 0)})"
            )
            print(f"[Token统计] 明细已保存: {token_usage_path}")
        except Exception as e:
            print(f"\n[run_supervisor] Token 统计写入失败（不中断主进程）: {type(e).__name__}: {e}")
            _persist_exception(e, where="token_tracker.write_report", round_i=99)
    return thread_id
