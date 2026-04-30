import os
import json
import time
import random
from typing import Annotated, Sequence, TypedDict, List, Any, Dict, Optional
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from deepagents import create_deep_agent, FilesystemPermission, SubAgent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends import LocalShellBackend
from deepagents.profiles import _get_harness_profile, _HarnessProfile, _merge_profiles, _register_harness_profile
from k8s_sandbox import exec_in_sandbox

load_dotenv(override=True)

# --- 1. 基础配置与路径初始化 ---
llm = ChatOpenAI(
    api_key=os.environ["API_KEY"],
    model=os.environ["MODEL"],
    base_url=os.environ["API_BASE"],
    temperature=0, 
)

BASE_AGENT_PROMPT="你是一位专业的故障诊断智能体。请根据用户的问题，检查 K8s 集群中的异常实例。"

_register_harness_profile(
    "openai",
    _merge_profiles(_get_harness_profile("openai"), _HarnessProfile(base_system_prompt=BASE_AGENT_PROMPT)),
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

class TokenUsageTracker(BaseCallbackHandler):
    def __init__(self):
        self.totals: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.by_model: Dict[str, Dict[str, int]] = {}
        self.calls: List[Dict[str, Any]] = []
        self._llm_start_by_run_id: Dict[str, Dict[str, Any]] = {}

    def _add_usage(self, usage: Dict[str, Any], model_name: Optional[str]) -> Dict[str, int]:
        def _to_int(v: Any) -> int:
            try:
                return int(v or 0)
            except Exception:
                return 0

        prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion_tokens = _to_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        total_tokens = _to_int(usage.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        self.totals["prompt_tokens"] += prompt_tokens
        self.totals["completion_tokens"] += completion_tokens
        self.totals["total_tokens"] += total_tokens

        key = model_name or "unknown"
        bucket = self.by_model.setdefault(key, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["total_tokens"] += total_tokens
        return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}

    def on_llm_start(self, serialized, prompts, run_id=None, parent_run_id=None, **kwargs):
        if run_id is None:
            return
        llm_name = None
        try:
            llm_name = (serialized or {}).get("name")
        except Exception:
            llm_name = None
        tags = kwargs.get("tags")
        metadata = kwargs.get("metadata")
        self._llm_start_by_run_id[str(run_id)] = {
            "started_at": time.time(),
            "llm": llm_name,
            "n_prompts": len(prompts) if isinstance(prompts, list) else None,
            "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
            "tags": tags if isinstance(tags, list) else None,
            "metadata": metadata if isinstance(metadata, dict) else None,
        }

    def on_llm_end(self, response, run_id=None, **kwargs):
        usage = None
        model_name = None
        try:
            model_name = getattr(response, "llm_output", None) and response.llm_output.get("model_name")
        except Exception:
            model_name = None

        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        except Exception:
            usage = None

        if not isinstance(usage, dict):
            try:
                gens = getattr(response, "generations", None) or []
                if gens and gens[0] and gens[0][0]:
                    gi = getattr(gens[0][0], "generation_info", None) or {}
                    usage = gi.get("token_usage") or gi.get("usage")
                    if model_name is None:
                        model_name = gi.get("model_name") or gi.get("model")
            except Exception:
                usage = None

        if isinstance(usage, dict):
            normalized = self._add_usage(usage, model_name)
            meta = self._llm_start_by_run_id.pop(str(run_id), {}) if run_id is not None else {}
            started_at = meta.get("started_at")
            ended_at = time.time()
            duration_s = None
            if isinstance(started_at, (int, float)):
                duration_s = round(ended_at - started_at, 6)
            self.calls.append(
                {
                    "run_id": str(run_id) if run_id is not None else None,
                    "parent_run_id": meta.get("parent_run_id"),
                    "llm": meta.get("llm"),
                    "model": model_name or "unknown",
                    "prompt_tokens": normalized["prompt_tokens"],
                    "completion_tokens": normalized["completion_tokens"],
                    "total_tokens": normalized["total_tokens"],
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration_s": duration_s,
                    "n_prompts": meta.get("n_prompts"),
                    "tags": meta.get("tags"),
                    "metadata": meta.get("metadata"),
                }
            )

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
    main_instructions = f"""# Role: Kubernetes 巡检任务总负责人 (Supervisor)

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
4.  **# 修复建议**：提供具备可执行性的 `kubectl` 指令或优化方案。

---

## 4. 强制约束 (Hard Constraints)

*   **拒绝早退**：如果对话历史中没有出现具体的节点状态或 Pod 报错细节，严禁输出“任务结束”。
*   **禁止冗余**：不要解释“我正在做什么”，直接执行指令。
*   **变量替换**：请确保所有路径中的 `{thread_id}` 被实际的任务 ID 替换。
*   **连续执行**：规划任务完成后，无需等待用户确认，应立即开始执行巡检指令。
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
        "callbacks": []
    }

    token_tracker = TokenUsageTracker()
    config["callbacks"].extend([ToolEventPrinter(), token_tracker])
    
    for chunk in agent.stream(initial_state, config):
        if "agent" in chunk:
            msg = chunk["agent"]["messages"][-1]
            print(f"\n--- [Supervisor]: {msg.content}")
        elif "call_subagent" in chunk:
            print(f"\n--- [调度专家]: {chunk['call_subagent']}")

    token_usage_path = os.path.join(reports_dir, f"token_usage-{thread_id}.json")
    with open(token_usage_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "thread_id": thread_id,
                "totals": token_tracker.totals,
                "by_model": token_tracker.by_model,
                "calls": token_tracker.calls,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n[Token统计] total={token_tracker.totals.get('total_tokens', 0)} "
          f"(prompt={token_tracker.totals.get('prompt_tokens', 0)}, completion={token_tracker.totals.get('completion_tokens', 0)})")
    print(f"[Token统计] 明细已保存: {token_usage_path}")
    print(f"\n[任务结束] 报告已生成在 {reports_dir} 目录。")

if __name__ == "__main__":
    thread_id = f"k8s_multi_agent_{random.randint(0, 100000)}"
    run_inspection(thread_id)
