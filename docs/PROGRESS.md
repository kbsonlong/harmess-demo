# PROGRESS.md

本文件用于记录“研发过程”的人工进度、关键决策与未决事项，便于跨会话恢复上下文。

运行时的机器可读进度与证据请以 `reports/` 目录为准：
- reports/todos.json
- reports/internal_states.json
- reports/sandbox_inspector-<thread_id>.json
- reports/inspection_report-<thread_id>.md

## 当前状态

- 已具备：Kind 集群拉起脚本、沙箱 Pod、sandbox_inspector（run/focus）、多智能体 Supervisor + 子专家的工作流约束
- 待完善：按需求补充检查项、扩展子智能体角色、完善报告模板与验收用例

## 关键决策

- 集群操作默认走沙箱（exec_in_sandbox），减少本机直连与权限风险
- 输出以结构化 JSON 为核心，避免大段 kubectl 输出导致上下文膨胀

## 未决事项

- （填）要覆盖的巡检维度清单（Node / Workload / Network / Storage / Security / Performance）
- （填）最小 RBAC 集合与是否允许写操作（如需）

