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


def create_subagents(
    llm,
    *,
    infra_expert_prompt: Optional[str] = None,
    workload_expert_prompt: Optional[str] = None,
    platform_expert_prompt: Optional[str] = None,
    access_expert_prompt: Optional[str] = None,
    fault_expert_prompt: Optional[str] = None,
    include_subagents: Optional[list[str]] = None,
) -> list[SubAgent]:
    infra_expert = SubAgent(
        name="infra_expert",
        description="基础设施专家。负责检查 K8s 节点(Node)状态、污点、资源压力，以及存储卷(PV/PVC)和网络组件。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=infra_expert_prompt
        or "你是一位基建专家。请执行诊断并只返回异常摘要。严禁输出正常的 Pod 列表，只说结论。",
    )

    workload_expert = SubAgent(
        name="workload_expert",
        description="工作负载专家。负责检查 kube-system 组件健康度、业务 Pod 状态、异常 Events 和错误日志。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=workload_expert_prompt
        or "你是一位负载专家。请使用过滤命令寻找 CrashLoopBackOff 或 Error 事件。只向主智能体汇报需要关注的问题。",
    )

    fault_expert = SubAgent(
        name="fault_expert",
        description="故障诊断专家。负责检查异常工作负载状态、异常 Events 和关键错误日志，并给出可复现证据与修复建议。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=fault_expert_prompt
        or "你是一位故障诊断专家。请使用过滤命令寻找 CrashLoopBackOff 或 Error 等异常，并只返回异常摘要与证据。",
    )

    platform_expert = SubAgent(
        name="platform_expert",
        description="平台组件专家。负责检查 Istio/OpenKruise 等平台组件异常与相关控制面问题。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=platform_expert_prompt
        or "你是一位平台组件专家。请只返回平台组件相关异常摘要与证据。",
    )

    access_expert = SubAgent(
        name="access_expert",
        description="访问与准入专家。负责检查认证/鉴权/RBAC/准入控制异常（Forbidden/Webhook/策略拒绝）。",
        model=llm,
        tools=[exec_in_sandbox, victorialogs_query],
        system_prompt=access_expert_prompt
        or "你是一位访问与准入专家。请只返回鉴权与准入相关异常摘要与证据。",
    )

    by_name = {
        "infra_expert": infra_expert,
        "workload_expert": workload_expert,
        "fault_expert": fault_expert,
        "platform_expert": platform_expert,
        "access_expert": access_expert,
    }

    include = include_subagents or ["infra_expert", "workload_expert", "platform_expert", "access_expert"]
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
    infra_expert_prompt: Optional[str] = None,
    workload_expert_prompt: Optional[str] = None,
    platform_expert_prompt: Optional[str] = None,
    access_expert_prompt: Optional[str] = None,
    fault_expert_prompt: Optional[str] = None,
    include_subagents: Optional[list[str]] = None,
    backend: Optional[Any] = None,
    checkpointer: Optional[MemorySaver] = None,
):
    backend = backend or FilesystemBackend(root_dir=paths.project_dir, virtual_mode=True)
    checkpointer = checkpointer or MemorySaver()

    subagents = create_subagents(
        llm,
        infra_expert_prompt=infra_expert_prompt,
        workload_expert_prompt=workload_expert_prompt,
        platform_expert_prompt=platform_expert_prompt,
        access_expert_prompt=access_expert_prompt,
        fault_expert_prompt=fault_expert_prompt,
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
