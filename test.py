import os
import random
from pathlib import Path

from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from agent_core.config import create_llm_from_env, get_project_paths
from agent_core.notify import notify_wecom_if_configured
from agent_core.profile import load_profile, load_profile_from_path
from agent_core.runtime import create_supervisor_agent, run_supervisor
from deepagents.profiles import _get_harness_profile, _HarnessProfile, _merge_profiles, _register_harness_profile


BASE_AGENT_PROMPT = "你是一位专业的故障诊断智能体。请根据用户的问题，检查 K8s 集群中的异常实例。"
DEFAULT_INITIAL_USER_MESSAGE = "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"


def build_dev01_supervisor_prompt() -> str:
    return """# Role: Kubernetes 巡检任务总负责人 (Supervisor)

## 1. 核心定位
你作为 Kubernetes 集群巡检与诊断的最高统筹者，负责把一次任务组织成闭环：先规划，再执行取证，再校验准出，最后汇总交付报告并落盘。

## 2. 严格工作流（不可跳过）

### 第一阶段：规划与落盘
*   **规划**：你必须基于管理员预设工作流程 + 用户意图输出路径选择与任务清单。
*   **落盘 TODO**：调用 `write_todos` 初始化所有任务项，写入 `/reports/todos.json`。

### 第二阶段：执行取证与诊断
*   **执行**：调度 `executor` 按任务清单采集证据与诊断，必须输出编号证据（E1/E2/...）并在结论中引用。
*   **发布失败上下文（如存在则必做）**：若输入包含 release_failure 元数据（release_id/targets/time_window），后续诊断必须围绕该时间窗收敛，并用 `victorialogs_query` 多路检索（应用日志 / k8s-events / argocd / kube-system）。

### 第三阶段：校验准出（核心）
*   **校验**：调度 `validator` 审核证据链与路径一致性；不通过则给出最小补采清单，由 `executor` 补齐后复审。
*   **执行锁**：严禁在未获得 `executor/validator` 的 Observation（含证据/审计结果）前更新 TODO 状态。

### 第四阶段：数据汇总与持久化
*   **中间态保存**：将规划、证据、根因分析与校验结果汇总并暂存至 `/reports/internal_states-{thread_id}.json`。

### 第五阶段：最终交付
*   **准出准则**：仅当所有 TODO 项均为 `completed` 且已获得具体专家证据时，方可生成报告。
*   **报告路径**：`/reports/inspection_report-{thread_id}.md`。

---

## 3. 报告交付规范 (Markdown Format)

报告必须严格包含以下部分：
1.  **# 巡检概要**：集群健康度总结、异常总数统计。
2.  **# 异常资源清单**：以表格形式列出受影响的 Namespace、资源类型、名称。
3.  **# 深度诊断详情**：
    *   **现象描述**：Subagent 获取的原始报错。
    *   **根因分析**：结合日志与状态给出的技术推断。
4.  **# 时间线**：发布/故障注入/首次观测/关键事件/关键日志/回滚点按时间排序。
5.  **# 证据索引**：为证据分配编号（E1/E2/...），包含 LogsQL 或命令、时间窗与关键片段。
6.  **# 回滚点**：给出最小回滚策略与验证点（需要显式确认才可执行写操作）。
7.  **# 修复建议**：提供具备可执行性的 `kubectl` 指令或优化方案。

---

## 4. 强制约束 (Hard Constraints)

*   **拒绝早退**：如果对话历史中没有出现具体的节点状态或 Pod 报错细节，严禁输出“任务结束”。
*   **禁止冗余**：不要解释“我正在做什么”，直接执行指令。
*   **变量替换**：请确保所有路径中的 `{thread_id}` 被实际的任务 ID 替换。
*   **连续执行**：规划任务完成后，无需等待用户确认，应立即开始执行巡检指令。
"""

_register_harness_profile(
    "openai",
    _merge_profiles(_get_harness_profile("openai"), _HarnessProfile(base_system_prompt=BASE_AGENT_PROMPT)),
)


def main():
    load_dotenv(override=True)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    paths = get_project_paths(project_dir)
    os.makedirs(paths.reports_dir, exist_ok=True)

    current_env = os.environ.copy()
    backend = LocalShellBackend(root_dir=project_dir, env=current_env, virtual_mode=True)
    checkpointer = MemorySaver()

    supervisor_llm = create_llm_from_env(prefix="SUPERVISOR")
    subagent_llm = create_llm_from_env(prefix="SUBAGENT")

    profile_path = os.environ.get("AGENT_PROFILE_PATH")
    profile_name = os.environ.get("AGENT_PROFILE") or "testbench"
    profile = load_profile_from_path(Path(profile_path)) if profile_path else load_profile(paths, profile_name)

    supervisor_prompt = profile.supervisor_prompt or build_dev01_supervisor_prompt()
    agent = create_supervisor_agent(
        supervisor_llm=supervisor_llm,
        subagent_llm=subagent_llm,
        paths=paths,
        supervisor_prompt=supervisor_prompt,
        executor_prompt=profile.executor_prompt,
        validator_prompt=profile.validator_prompt,
        workflow_md=profile.workflow_md,
        include_subagents=profile.include_subagents,
        backend=backend,
        checkpointer=checkpointer,
    )

    initial_user_message = profile.initial_user_message or DEFAULT_INITIAL_USER_MESSAGE
    thread_id = run_supervisor(
        agent=agent,
        initial_user_message=initial_user_message,
        thread_id=f"k8s_multi_agent_{random.randint(0, 100000)}",
        recursion_limit=profile.recursion_limit or 200,
        reports_dir=paths.reports_dir,
    )
    notify_wecom_if_configured(Path(paths.reports_dir), thread_id=thread_id)


if __name__ == "__main__":
    main()
