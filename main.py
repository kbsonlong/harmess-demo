import os
from langchain_litellm import ChatLiteLLM
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from k8s_sandbox import create_sandbox, exec_in_sandbox, render_sandbox_manifests
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
load_dotenv(override=True)

llm = ChatLiteLLM(
    custom_llm_provider="openai",
    api_base=os.environ["API_BASE"],
    model=os.environ["MODEL"],
    api_key=os.environ["API_KEY"],
)
# System prompt to steer the agent to be an expert researcher
research_instructions = """您是一位资深的Kubernetes管理员。您的任务是进行Kubernetes集群的健康检查，检查集群是否正常，并撰写一份巡检报告。

您可以使用以下工具来获取Kubernetes集群的健康检查相关的信息。

## `exec_in_sandbox`

使用此工具在沙箱环境中执行命令。在此工具中，您可以执行Kubernetes命令，例如`kubectl get nodes`、`kubectl get pods`等只读权限命令。

优先使用 `label_selector` 来选择目标 Pod（例如使用 `create_sandbox` 返回的 `label_selector`），工具会从匹配的实例中选择一个进行执行。

"""


project_dir = os.path.dirname(os.path.abspath(__file__))

backend = FilesystemBackend(root_dir=project_dir, virtual_mode=False)
checkpointer = MemorySaver()


def main():
    print(project_dir + "/skills/")
    create_sandbox(rbac_profile="cluster-readonly", wait_ready=True)
    agent = create_deep_agent(
        model=llm,
        tools=[render_sandbox_manifests, exec_in_sandbox],
        system_prompt=(
            research_instructions
            + "\n\n约束：沙箱已在巡检开始前由系统创建。你不得尝试创建/修改任何 RBAC 或提权操作。"
            + "当遇到权限不足（Forbidden/Unauthorized 或 can-i 返回 no）时，跳过该检查项，"
            + "并在巡检报告中单独标记“缺少权限”，由管理员对固定 Role/ClusterRole 进行授权。"
        ),
        skills=[os.path.join(project_dir, "skills")],
        interrupt_on={
            "write_file": True,  # Default: approve, edit, reject
            "read_file": False,  # No interrupts needed
            "edit_file": True    # Default: approve, edit, reject
        },
        backend=backend,
        checkpointer=checkpointer,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user", 
                    "content": "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"
                }
            ]
        },
        {"configurable": {"thread_id": "demo04"}},
    )

    # Print the agent's response
    content = result["messages"][-1].content
    print(content)



if __name__ == "__main__":
    main()
