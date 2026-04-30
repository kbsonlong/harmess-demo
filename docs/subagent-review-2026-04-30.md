# Subagent 定义评审（审视视角）

日期：2026-04-30

范围：本项目 Kubernetes 巡检与健康检查 Agent 的 subagent 设计（职责边界、输出契约、可维护性、可复现性）。

## 结论摘要

原始设计（仅 infra_expert + workload_expert，且另有 test.py 的 fault_expert 体系）存在边界重叠、输出契约弱、入口漂移等问题，导致：
- 证据链不稳定（结论不可复现、难以聚合）
- 平台组件与准入类问题没有一等公民角色
- 多入口脚本分裂，长期维护成本高

## 主要问题（Findings）

1. subagent 颗粒度过粗，基础设施与工作负载覆盖面过大，平台组件（Istio/OpenKruise）与准入/RBAC 不具备专门责任人。
2. infra_expert 与 workload_expert 的职责边界存在重叠（kube-system/网络组件），容易推诿或重复采集。
3. 输出契约过弱：缺少固定结构（严重级别/证据/影响面/下一步），Supervisor 聚合难、准出不可控。
4. 缺少输出预算控制：events/logs 易爆量，污染上下文并拖慢执行。
5. 角色能力边界不明显：所有 subagent 实际工具一致，角色分工依赖 prompt 文案而非机制。
6. 技能使用未被强约束：虽然有 skills/k8s-inspector，但 subagent 不一定按一致流程执行。
7. 环境类型差异缺少落点：AWS EC2 自建与托管 IDC 自建存在排障假设差异，但 subagent 体系未体现。
8. 仓库存在两套 subagent 体系：main/runtime 与 test.py 的 fault_expert 分叉，导致漂移风险。

## 优化目标（Definition of Done）

- subagent 角色覆盖面完整且边界清晰：infra / workload / platform / access 四类问题有明确归属。
- 每条诊断输出具备“可复现证据”：命令 + 关键输出摘要 + 影响面 + 修复建议/下一步验证命令。
- 输出可控：强制过滤异常项，日志/证据截断。
- 多入口脚本共享同一套 subagent 定义与 profile/prompts，避免漂移。

