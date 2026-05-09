import os
from pathlib import Path

from dotenv import load_dotenv

from agent_core.config import create_llm_from_env, get_project_paths
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
        planner_prompt=profile.planner_prompt,
        executor_prompt=profile.executor_prompt,
        validator_prompt=profile.validator_prompt,
        workflow_md=profile.workflow_md,
        include_subagents=profile.include_subagents,
    )

    initial_user_message = profile.initial_user_message or "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"
    
    thread_id = run_supervisor(
        agent=agent,
        initial_user_message=initial_user_message,
        recursion_limit=profile.recursion_limit or 150,
        reports_dir=paths.reports_dir,
    )
    notify_wecom_if_configured(Path(paths.reports_dir), thread_id=thread_id)



if __name__ == "__main__":
    main()
