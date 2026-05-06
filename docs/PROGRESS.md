# PROGRESS.md

本文件用于记录“研发过程”的人工进度、关键决策与未决事项，便于跨会话恢复上下文。

运行时的机器可读进度与证据请以 `reports/` 目录为准：
- reports/todos.json
- reports/internal_states.json
- reports/sandbox_inspector-<thread_id>.json
- reports/inspection_report-<thread_id>.md

## 当前状态

- 已具备：Kind 集群拉起脚本、沙箱 Pod、sandbox_inspector（run/focus）、多智能体 Supervisor + 子专家的工作流约束
- 已具备：Kind + Argo CD + demo-app（GitOps）最小清单与发布元数据落盘入口（`reports/release_metadata-<thread_id>.json`）
- 已修复：Argo CD 安装 kustomize 显式 `namespace: argocd`，并增强 `gitops_demo.py` 的 Argo CD 就绪等待（ns/CRD Established/deploy --all Available）
- 已具备：GitOps 失败发布模拟器（imagepull/crashloop），落盘 `reports/release_failure-<thread_id>.json`（release_id/对象/时间窗）
- 已具备：VictoriaLogs 单实例 + Fluent Bit 容器日志采集 + event-exporter 事件采集（均写入 VictoriaLogs）的最小清单（Kustomize）
- 已修复：Fluent Bit 写入 VictoriaLogs 的 _msg 映射（_msg_field=message，Lua 兜底 message=log），避免查询结果出现 “missing _msg field”
- 已具备：VictoriaLogs 查询工具（基于 exec_in_sandbox 的 Python HTTP 调用）并接入运行时；修复 victorialogs_query 缺失 docstring 导致 Tool 创建失败；main.py 自动注入最新 release_failure 上下文；报告模板增加 时间线/证据索引/回滚点 章节要求
- 待完善：按需求补充检查项、扩展子智能体角色、完善报告模板与验收用例

## 关键决策

- 集群操作默认走沙箱（exec_in_sandbox），减少本机直连与权限风险
- 输出以结构化 JSON 为核心，避免大段 kubectl 输出导致上下文膨胀
- `run_supervisor()` 已恢复统一 Token 统计与 `reports/token_usage-<thread_id>.json` 落盘；`testbench` profile/prompts 与 `test.py` 默认 prompts 已对齐为同一套文案
- kind_demo.py/gitops_demo.py 默认使用项目内 `.demo/kubeconfig`（未设置 `KUBECONFIG` 时自动注入），绕过 `~/.kube/config` 写限制；Kind demo 仍需给 Kind 节点补 `biz.type=common` 标签后才能调度 `k8s-sandbox`
- `sandbox_inspector._check_pods()` 已修复为扫描所有非 `Succeeded` Pod 后再过滤异常，避免漏报 `phase=Running` 但容器处于 `CrashLoopBackOff` 的场景；Kind demo 二次验证已确认修复生效
- 日志写入 VictoriaLogs 统一走 `/insert/jsonline`，容器日志由 Fluent Bit 发出，Events 由 event-exporter webhook 直推

## 未决事项

- （填）要覆盖的巡检维度清单（Node / Workload / Network / Storage / Security / Performance）
- （填）最小 RBAC 集合与是否允许写操作（如需）
