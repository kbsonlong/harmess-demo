import os
from langchain_litellm import ChatLiteLLM
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from deepagents import create_deep_agent,FilesystemPermission
from deepagents.backends.filesystem import FilesystemBackend
from k8s_sandbox import exec_in_sandbox
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
import json
import time
load_dotenv(override=True)

# import warnings
# from pydantic import PydanticSerializerWarnings

# # 忽略 Pydantic 的序列化警告
# warnings.filterwarnings("ignore", category=PydanticSerializerWarnings)

# llm = ChatLiteLLM(
#     custom_llm_provider="openai",
#     api_base=os.environ["API_BASE"],
#     model=os.environ["MODEL"],
#     api_key=os.environ["API_KEY"],
# )

llm = ChatOpenAI(
    api_key=os.environ["API_KEY"],
    model=os.environ["MODEL"],
    base_url=os.environ["API_BASE"],
)

# System prompt to steer the agent to be an expert researcher
research_instructions = """您是一位资深的Kubernetes管理员。您的任务是进行Kubernetes集群的健康检查，检查集群是否正常，并撰写一份巡检报告。

您可以使用以下工具来获取Kubernetes集群的健康检查相关的信息。

## `exec_in_sandbox`

使用此工具在  Kubernetes 集群沙箱环境中执行命令,用于获取集群状态和排查集群问题。

"""

base_instructions = """您是一位资深的 Kubernetes 管理员。您的任务是进行 Kubernetes 集群的健康检查，检查集群是否正常，并撰写一份巡检报告。
你可以通过调用 skills 目录下的工具来获取特定任务（如巡检、安全审计、性能调优）的专业指南。
当用户提出复杂任务时：
1. 先搜索并调用相关的 tool 和 skills。
2. 严格按照指南中的步骤执行，不要跳步。
3. 只有当指南中要求的所有检查点都完成后，才输出最终结果。

## `exec_in_sandbox`

使用此工具在  Kubernetes 集群沙箱环境中执行命令,用于获取集群状态和排查集群问题。
"""


project_dir = os.path.dirname(os.path.abspath(__file__))

backend = FilesystemBackend(root_dir=project_dir, virtual_mode=False)
checkpointer = MemorySaver()

def _format_message_content(content):
    try:
        if isinstance(content, (dict,list)):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)
    except Exception as e:
        return str(content)

class ToolEventPrinter(BaseCallbackHandler):
    def __init__(self):
        self._start_time_by_run_id = {}

    def on_tool_start(self, serialized, input_str=None, inputs=None, run_id=None, parent_run_id=None, **kwargs):
        name = None
        if isinstance(serialized, dict):
            name = serialized.get("name") or serialized.get("id")
        name = name or "unknown_tool"
        payload = inputs if inputs is not None else input_str
        if run_id is not None:
            self._start_time_by_run_id[run_id] = time.perf_counter()
        print(f"\n[tool:start] {name} run_id={run_id} parent_run_id={parent_run_id}\n{_format_message_content(payload)}\n")

    def on_tool_end(self, output, run_id=None, parent_run_id=None, **kwargs):
        duration_s = None
        if run_id is not None:
            start = self._start_time_by_run_id.pop(run_id, None)
            if start is not None:
                duration_s = time.perf_counter() - start
        duration_part = f" duration_s={duration_s:.3f}" if duration_s is not None else ""
        print(f"\n[tool:end] run_id={run_id} parent_run_id={parent_run_id}{duration_part}\n{_format_message_content(output)}\n")

    def on_tool_error(self, error, run_id=None, parent_run_id=None, **kwargs):
        print(f"\n[tool:error] run_id={run_id} parent_run_id={parent_run_id}\n{error}\n")


def main():
    print(project_dir + "/skills/")
    agent = create_deep_agent(
        model=llm,
        tools=[exec_in_sandbox],
        system_prompt=(
            base_instructions
            + "\n\n约束：沙箱已在巡检开始前由系统创建。你不得尝试创建/修改任何 RBAC 或提权操作。"
            + "当遇到权限不足（Forbidden/Unauthorized 或 can-i 返回 no）时，跳过该检查项，"
            + "并在巡检报告中单独标记“缺少权限”，由管理员对固定 Role/ClusterRole 进行授权。"
            + "巡检报告以 Markdown 格式输出到项目 reports/ 目录下。"
        ),
        skills=[os.path.join(project_dir, "skills")],
        interrupt_on={
            "write_file": False,  # Default: approve, edit, reject
            "read_file": False,  # No interrupts needed
            "edit_file": False    # Default: approve, edit, reject
        },
        backend=backend,
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=[os.path.join(project_dir, "reports/**")],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read"],
                paths=[os.path.join(project_dir, "skills/**")],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
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
        {
            "configurable": {"thread_id": "demo04"},
            "callbacks": [ToolEventPrinter()],
        },
    )

    content = result["messages"][-1].content
    print(content)



if __name__ == "__main__":
    main()
