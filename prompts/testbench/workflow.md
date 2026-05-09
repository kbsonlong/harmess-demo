## 2. 动态工作流（根据意图选择路径）

### 第一阶段：意图识别与任务规划

* **路径 A (全量巡检)**：若用户输入为模糊指令（如“巡检”、“检查集群”），执行全量扫描。
* **路径 B (定向诊断)**：若用户提供了具体的 `Namespace`、`Service` 或 `Pod` （如“诊断命名空间下的服务异常”），跳过全量扫描，直接进入**第三阶段**提取相关资源的上下文。
* **任务规划**：无论哪种路径，立即调用 `write_todos` 初始化任务项，完成后立即执行。

### 第二阶段：全量扫描（仅路径 A）

* **执行巡检**：运行 `python -m sandbox_inspector.cli run --max-findings 100`。
* **结果固化**：将原始 JSON 保存至 `/reports/sandbox_inspector-$(date +%Y%m%d%H%M%S).json`。

### 第三阶段：动态指派与专家诊断（核心）

* **上下文提取**：
* **路径 A**：从巡检 JSON 中提取 `Error` 或 `Warning` 摘要。
* **路径 B**：直接针对用户指定的资源调用工具（如 `kubectl get/describe/logs`）获取原始异常信息。

* **智能分发**：根据故障内容指派对应的 **子智能体**（Subagent）进行诊断。关联多个故障时，需判断其相关性（如级联故障）。
* **执行锁**：必须收到子智能体的 Observation（诊断详情、根因分析）后，方可标记 TODO 为 `completed`。

### 第四阶段：数据汇总与持久化

* **中间态保存**：汇总所有子智能体返回的诊断详情、根因、时间线及证据，暂存至 `/reports/internal_states-$(date +%Y%m%d%H%M%S).json`。

### 第五阶段：最终交付

* **准出准则**：所有 TODO 为 `completed` 且获得专家诊断结果后生成报告。
* **报告路径**：`/reports/inspection_report-$(date +%Y%m%d%H%M%S).md`。
