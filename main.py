import os
from pathlib import Path

from dotenv import load_dotenv

from agent_core.config import create_llm_from_env, get_project_paths
from agent_core.release_context import load_latest_release_failure, summarize_release_failure_context
from agent_core.notify import notify_wecom_if_configured
from agent_core.profile import load_profile, load_profile_from_path
from agent_core.prompts import build_supervisor_prompt
from agent_core.runtime import create_supervisor_agent, run_supervisor

def main():
    load_dotenv(override=True)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    paths = get_project_paths(project_dir)
    llm = create_llm_from_env()
    profile_path = os.environ.get("AGENT_PROFILE_PATH")
    profile_name = os.environ.get("AGENT_PROFILE") or "default"
    profile = (
        load_profile_from_path(Path(profile_path)) if profile_path else load_profile(paths, profile_name)
    )
    supervisor_prompt = profile.supervisor_prompt or build_supervisor_prompt(paths)
    agent = create_supervisor_agent(
        llm=llm,
        paths=paths,
        supervisor_prompt=supervisor_prompt,
        infra_expert_prompt=profile.infra_expert_prompt,
        workload_expert_prompt=profile.workload_expert_prompt,
        platform_expert_prompt=profile.platform_expert_prompt,
        access_expert_prompt=profile.access_expert_prompt,
        fault_expert_prompt=profile.fault_expert_prompt,
        include_subagents=profile.include_subagents,
    )

    release_failure = load_latest_release_failure(
        reports_dir=paths.reports_dir,
        thread_id=os.environ.get("RELEASE_FAILURE_THREAD_ID"),
        explicit_path=os.environ.get("RELEASE_FAILURE_PATH"),
    )
    release_ctx = summarize_release_failure_context(release_failure) if isinstance(release_failure, dict) else None
    initial_user_message = profile.initial_user_message or "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"
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
        recursion_limit=profile.recursion_limit or 150,
        reports_dir=paths.reports_dir,
    )
    notify_wecom_if_configured(Path(paths.reports_dir), thread_id=thread_id)



if __name__ == "__main__":
    main()
