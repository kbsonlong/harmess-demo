import os
import random
from pathlib import Path

from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from agent_core.config import create_llm_from_env, get_project_paths
from agent_core.release_context import load_latest_release_failure, summarize_release_failure_context
from agent_core.notify import notify_wecom_if_configured
from agent_core.profile import load_profile, load_profile_from_path
from agent_core.runtime import create_supervisor_agent, run_supervisor
from deepagents.profiles import _get_harness_profile, _HarnessProfile, _merge_profiles, _register_harness_profile


BASE_AGENT_PROMPT = "你是一位专业的故障诊断智能体。请根据用户的问题，检查 K8s 集群中的异常实例。"
INFRA_EXPERT_PROMPT = "你是一位基建专家。请执行诊断并只返回异常摘要。严禁输出正常的 Pod 列表，只说结论。"
FAULT_EXPERT_PROMPT = (
    "你是一位故障诊断专家。请使用过滤命令寻找 CrashLoopBackOff 或 Error 等非 Running、Ready 状态的事件。"
    "只向主智能体汇报需要关注的问题。"
)
DEFAULT_INITIAL_USER_MESSAGE = "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"


def build_dev01_supervisor_prompt() -> str:
    return """# Role: Kubernetes 巡检任务总负责人 (Supervisor)

## 1. 核心定位
你作为 Kubernetes 集群巡检的最高统筹者，负责从环境检查、全量扫描到深度诊断的全流程闭环。你必须调度 `infra_expert`（基础设施专家）与 `fault_expert`（故障诊断专家）协同工作。

## 2. 严格工作流（不可跳过）

### 第一阶段：任务规划与环境确认
*   **任务规划**：立即调用 `write_todos` 初始化所有任务项。
*   **沙箱校验**：执行 `execute` 运行 `echo ok`。若失败，立即报错并停止。

### 第二阶段：全量扫描与初步解析
*   **执行巡检**：在沙箱内运行 `python -m sandbox_inspector.cli run --max-findings 50`。
*   **结果固化**：将巡检原始 JSON 保存至 `/reports/sandbox_inspector-{thread_id}.json`。
*   **异常提取**：解析 JSON 内容，提取所有 `Error` 或 `Warning` 级别的异常摘要。
*   **发布失败上下文（如存在则必做）**：若输入包含 release_failure 元数据（release_id/targets/time_window），后续诊断必须围绕该时间窗收敛，并用 `victorialogs_query` 多路检索（应用日志 / k8s-events / argocd / kube-system）。

### 第三阶段：动态指派与专家诊断（核心）
*   **智能分发**：
    *   **infra_expert**：负责处理 Node 状态、资源水位 (CPU/Mem)、Taints、PV/PVC、网络组件问题。
    *   **fault_expert**：负责处理 Pod 重启/挂起、日志报错 (Logs)、事件异常 (Events)。
*   **执行锁**：严禁在未获得子智能体 Observation 的情况下更新 TODO 状态。**必须收到专家的诊断详情后，方可标记该任务为 `completed`。**

### 第四阶段：数据汇总与持久化
*   **中间态保存**：将所有专家返回的诊断详情、根因分析汇总并暂存至 `/reports/internal_states-{thread_id}.json`。

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

    llm = create_llm_from_env()

    profile_path = os.environ.get("AGENT_PROFILE_PATH")
    profile_name = os.environ.get("AGENT_PROFILE") or "testbench"
    profile = load_profile_from_path(Path(profile_path)) if profile_path else load_profile(paths, profile_name)

    supervisor_prompt = profile.supervisor_prompt or build_dev01_supervisor_prompt()
    agent = create_supervisor_agent(
        llm=llm,
        paths=paths,
        supervisor_prompt=supervisor_prompt,
        infra_expert_prompt=profile.infra_expert_prompt or INFRA_EXPERT_PROMPT,
        workload_expert_prompt=profile.workload_expert_prompt,
        platform_expert_prompt=profile.platform_expert_prompt,
        access_expert_prompt=profile.access_expert_prompt,
        fault_expert_prompt=profile.fault_expert_prompt or FAULT_EXPERT_PROMPT,
        include_subagents=profile.include_subagents,
        backend=backend,
        checkpointer=checkpointer,
    )

    release_failure = load_latest_release_failure(
        reports_dir=paths.reports_dir,
        thread_id=os.environ.get("RELEASE_FAILURE_THREAD_ID"),
        explicit_path=os.environ.get("RELEASE_FAILURE_PATH"),
    )
    release_ctx = summarize_release_failure_context(release_failure) if isinstance(release_failure, dict) else None
    initial_user_message = profile.initial_user_message or DEFAULT_INITIAL_USER_MESSAGE
    if release_ctx:
        initial_user_message = (
            initial_user_message
            + "\n\n【GitOps 发布失败上下文】\n"
            + f"{release_ctx}\n"
            + "请优先围绕 release_id + time_window 做定位：\n"
            + "- 对 targets 做 focus 深挖（Events/Logs）\n"
            + "- 查询 VictoriaLogs：应用日志 / k8s-events / argocd / kube-system（限制条数、只取关键字段）\n"
            + "- 在最终报告加入：时间线 / 证据索引 / 回滚点\n"
        )
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
