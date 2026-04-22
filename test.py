import os
import json
import time
import random
from typing import Annotated, Sequence, TypedDict, List
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from deepagents import create_deep_agent, FilesystemPermission, SubAgent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends import LocalShellBackend
from k8s_sandbox import exec_in_sandbox

load_dotenv(override=True)

# --- 1. 基础配置与路径初始化 ---
llm = ChatOpenAI(
    api_key=os.environ["API_KEY"],
    model=os.environ["MODEL"],
    base_url=os.environ["API_BASE"],
    temperature=0, 
)

project_dir = os.path.dirname(os.path.abspath(__file__))
reports_dir = os.path.join(project_dir, "reports")
os.makedirs(reports_dir, exist_ok=True)

# backend = FilesystemBackend(root_dir=project_dir, virtual_mode=True)
current_env = os.environ.copy()
backend = LocalShellBackend(
    root_dir=project_dir,
    env=current_env,
    virtual_mode=True
)
checkpointer = MemorySaver()

# --- 2. 格式化与日志工具 ---
def _format_message_content(content):
    try:
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)
    except Exception:
        return str(content)

class ToolEventPrinter(BaseCallbackHandler):
    def __init__(self):
        self._start_time_by_run_id = {}
    def on_tool_start(self, serialized, input_str=None, inputs=None, run_id=None, **kwargs):
        if run_id: self._start_time_by_run_id[run_id] = time.perf_counter()
        print(f"\n[tool:start] {serialized.get('name') or 'unknown'}\n{_format_message_content(inputs or input_str)}")
    def on_tool_end(self, output, run_id=None, **kwargs):
        duration = time.perf_counter() - self._start_time_by_run_id.pop(run_id, 0) if run_id in self._start_time_by_run_id else 0
        print(f"\n[tool:end] duration={duration:.2f}s\n{_format_message_content(output)}")

# --- 3. 定义子智能体 (Sub-Agents) ---

infra_expert = SubAgent(
    name="infra_expert",
    description="基础设施专家。负责检查 K8s 节点(Node)状态、污点、资源压力，以及存储卷(PV/PVC)和网络组件。",
    model=llm,
    tools=[exec_in_sandbox],
    system_prompt="你是一位基建专家。请执行诊断并只返回异常摘要。严禁输出正常的 Pod 列表，只说结论。"
)

fault_expert = SubAgent(
    name="fault_expert",
    description="kubernetes 故障诊断专家。负责检查异常实例运行状态、异常 Events 和错误日志。",
    model=llm,
    tools=[exec_in_sandbox],
    system_prompt="你是一位故障诊断专家。请使用过滤命令寻找 CrashLoopBackOff 或 Error 等非 Running、Ready 状态的事件。只向主智能体汇报需要关注的问题。"
)



# --- 5. 核心运行函数 ---
def run_inspection(thread_id: str):
    print(f"正在启动多智能体协作巡检 (Thread: {thread_id})...")

    # --- 4. 主智能体指令 ---
    main_instructions = f"""您是 Kubernetes 巡检任务的总负责人（Supervisor）。

### 严格工作流（不可跳过）：
1. **任务规划**：调用 `write_todos` 列出详细任务。
  1) 先确认沙箱可用（exec echo ok）。
  2) 在沙箱内执行 `python -m sandbox_inspector.cli run --max-findings 50` 获取巡检结果,沙箱内已经存在脚本,请直接执行。
  3) 将巡检结果保存到 `/reports/sandbox_inspector-{thread_id}.json` 中
  4) 解析巡检结果，提取异常摘要。
  5) 根据异常摘要，指派 `infra_expert` 或 `fault_expert` 执行详细诊断。
  6) 等待 subagent 完成诊断，汇总异常摘要，完成任务。
  7) 生成最终 Markdown 报告。
  请注意：规划完任务后，请立即开始指派 subagent 执行巡检指令 `python -m sandbox_inspector.cli run --max-findings 50`。不要停下。
2. **任务指派（核心）**：
   - 根据 `infra_expert` 和 `fault_expert` 的角色，动态分配任务。
   - **严禁**在未获得子智能体回复的情况下更新 TODO 状态。
   - 只有收到专家的观察结果（Observation），才算该项完成。
3. **数据汇总**：将专家返回的异常信息暂存在 `/reports/internal_states-{thread_id}.json` 中。
4. **最终交付**：巡检报告 `/reports/inspection_report-{thread_id}.md`
   - 只有当所有 TODO 标记为 `completed` 后，才允许输出最终报告 `/reports/inspection_report-{thread_id}.md` 。
   - 报告内容必须包含所有异常摘要，根因分析以及修复建议。
   - 报告格式必须符合 Markdown 规范，包括标题、段落、列表等。

### ⚠️ 拒绝早退提醒：
如果你的对话历史中没有出现专家的诊断详情（如节点状态、Pod 报错），严禁输出“任务结束”或生成报告。
"""

    # 创建主智能体
    agent = create_deep_agent(
        model=llm,
        tools=[], 
        subagents=[fault_expert], # 动态任务分配的核心
        system_prompt=main_instructions,
        # skills=[os.path.join(project_dir, "skills")],
        interrupt_on={
            "write_file": False,  # Default: approve, edit, reject
            "read_file": False,  # No interrupts needed
            "edit_file": False    # Default: approve, edit, reject
        },
        backend=backend,
        # permissions=[
        #     FilesystemPermission(operations=["read", "write"], paths=[f"{reports_dir}/**"], mode="allow"),
        #     FilesystemPermission(operations=["read"], paths=[os.path.join(project_dir, "skills/**")], mode="allow")
        # ],
        checkpointer=checkpointer,
    )

    initial_state = {
        "messages": [
            {
                "role": "user", 
                "content": "对 Kubernetes 集群做巡检与健康检查,如果有异常,请报告异常信息并提供修复方案"
            }
        ]
    }

    config = {
        "configurable": {"thread_id": thread_id}, 
        "recursion_limit": 150, # 多 Agent 协作需要更高的步数上限
        "callbacks": [ToolEventPrinter()]
    }
    
    for chunk in agent.stream(initial_state, config):
        if "agent" in chunk:
            msg = chunk["agent"]["messages"][-1]
            print(f"\n--- [Supervisor]: {msg.content}")
        elif "call_subagent" in chunk:
            print(f"\n--- [调度专家]: {chunk['call_subagent']}")

    print(f"\n[任务结束] 报告已生成在 {reports_dir} 目录。")

if __name__ == "__main__":
    thread_id = f"k8s_multi_agent_{random.randint(0, 100000)}"
    run_inspection(thread_id)
