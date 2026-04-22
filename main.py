import os
import random
from langchain_litellm import ChatLiteLLM
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from deepagents import create_deep_agent,FilesystemPermission, SubAgent
from deepagents.backends.filesystem import FilesystemBackend
from k8s_sandbox import exec_in_sandbox
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
import json
import time
load_dotenv(override=True)


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

project_dir = os.path.dirname(os.path.abspath(__file__))

backend = FilesystemBackend(root_dir=project_dir, virtual_mode=True)
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

infra_expert = SubAgent(
    name="infra_expert",
    description="基础设施专家。负责检查 K8s 节点(Node)状态、污点、资源压力，以及存储卷(PV/PVC)和网络组件。",
    model=llm,
    tools=[exec_in_sandbox],
    system_prompt="你是一位基建专家。请执行诊断并只返回异常摘要。严禁输出正常的 Pod 列表，只说结论。"
)

workload_expert = SubAgent(
    name="workload_expert",
    description="工作负载专家。负责检查 kube-system 组件健康度、业务 Pod 状态、异常 Events 和错误日志。",
    model=llm,
    tools=[exec_in_sandbox],
    system_prompt="你是一位负载专家。请使用过滤命令寻找 CrashLoopBackOff 或 Error 事件。只向主智能体汇报需要关注的问题。"
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


main_instructions = f"""您是 Kubernetes 巡检任务的总负责人（Supervisor）。
### 严格工作流（不可跳过）：
1. **任务规划**：调用 `write_todos` 列出详细任务,将 TODO 暂存在 `{project_dir}/reports/todos.json` 中。
2. **任务指派（核心）**：
   - 根据 `infra_expert` 和 `workload_expert` 的角色，动态分配任务。
   - **严禁**在未获得子智能体回复的情况下更新 TODO 状态。
   - 只有收到专家的观察结果（Observation），才算该项完成。
3. **数据汇总**：将专家返回的异常信息暂存在 `{project_dir}/reports/internal_states.json` 中。
4. **最终交付**：只有当 `{project_dir}/reports/todos.json` 中的所有 TODO 标记为 `completed` 后，才允许输出最终报告。

### ⚠️ 拒绝早退提醒：
如果你的对话历史中没有出现专家的诊断详情（如节点状态、Pod 报错），严禁输出“任务结束”或生成报告。
请注意：规划完任务后，请立即开始指派 subagent 执行任务。不要停下。
"""


def main():
    print(project_dir + "/skills/")
    agent = create_deep_agent(
        model=llm,
        tools=[exec_in_sandbox],
        system_prompt=main_instructions,
        subagents=[infra_expert, workload_expert], # 动态任务分配的核心
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

    thread_id = f"k8s_multi_agent_{random.randint(0, 100000)}"

    config = {
        "configurable": {"thread_id": thread_id}, 
        "recursion_limit": 150, # 多 Agent 协作需要更高的步数上限
        "callbacks": [ToolEventPrinter()]
    }

    initial_state = {
        "messages": [
            {
                "role": "user", 
                "content": "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"
            }
        ]
    }
    for chunk in agent.stream(initial_state, config):
        if "agent" in chunk:
            msg = chunk["agent"]["messages"][-1]
            print(f"\n--- [Supervisor]: {msg.content}")
        elif "call_subagent" in chunk:
            print(f"\n--- [调度专家]: {chunk['call_subagent']}")

    # content = result["messages"][-1].content
    # print(content)



if __name__ == "__main__":
    main()
