# GitOps 发布失败诊断（VictoriaLogs + deepAgent）Spec

## Why
当前仓库具备“沙箱执行 + 结构化巡检 JSON + 诊断报告”能力，但缺少一套可复现实验场景：GitOps 发布、统一日志采集与发布失败后的自动化诊断闭环。

## What Changes
- 提供一套 **GitOps 发布 Demo 流程**：以 Git 仓库变更作为发布事件，驱动集群内持续交付工具同步（默认 Argo CD）。
- 部署 **VictoriaLogs 日志存储**，并在集群内布置日志采集组件，将以下信号统一写入 VictoriaLogs：
  - 发布事件与发布控制面日志（GitOps 控制器日志 + 自定义发布事件日志）
  - Kubernetes Events
  - 容器运行日志（/var/log/containers）
  - kube-system 关键组件日志（如 coredns、kube-proxy、kube-apiserver 等）
- 增加 **发布失败模拟器**：以可配置方式制造典型失败（例如 ImagePullBackOff / CrashLoopBackOff / Readiness 探针失败）。
- 扩展 **deepAgent 诊断链路**：当发布失败被检测到时，自动执行：
  - sandbox_inspector 的 run/focus 结构化巡检
  - VictoriaLogs 查询（按发布窗口、应用标签、namespace、组件等过滤）并抓取关键日志片段
  - 生成诊断报告（Markdown），并将相关结构化证据落盘到 reports/
- 增加配置项（环境变量/配置文件）用于控制：
  - GitOps 工具与端点、应用名/namespace
  - VictoriaLogs 地址与查询时间窗
  - 失败模式与触发方式

## Impact
- Affected specs: GitOps 发布演示、日志采集/查询、故障注入、自动化诊断报告
- Affected code: main.py / agent_core/runtime.py / prompts/** / sandbox_inspector/**（以最小侵入方式扩展）、新增部署与演示入口（scripts/manifests）

## ADDED Requirements

### Requirement: GitOps 发布工作流
系统 SHALL 提供一个可重复执行的发布工作流，用于在本地 kind 集群中演示 GitOps 同步与发布事件。

#### Scenario: Success case
- **WHEN** 用户执行发布命令（例如指定应用与 git revision）
- **THEN** GitOps 工具检测到 Git 仓库变更并完成同步
- **AND** 集群中目标工作负载达到期望就绪状态（Ready/Available 与期望一致）
- **AND** 产生可关联的发布元数据（release_id、commit_sha、app、namespace、start_time、end_time）

### Requirement: 日志与事件采集到 VictoriaLogs
系统 SHALL 将发布相关信号、集群事件与运行日志持续写入 VictoriaLogs，并可按发布元数据进行检索。

#### Scenario: Success case
- **WHEN** 集群产生 Events、Pod/容器日志、kube-system 组件日志、GitOps 控制器日志
- **THEN** VictoriaLogs 中可在约定时间窗内检索到对应日志
- **AND** 日志至少包含可关联字段：namespace、pod、container、app（或工作负载标签）、release_id（若适用）

### Requirement: 发布失败模拟
系统 SHALL 支持至少两种可配置的发布失败模式，用于稳定复现并触发诊断流程。

#### Scenario: Failure injection
- **WHEN** 用户选择失败模式并执行发布
- **THEN** 目标工作负载进入失败状态（例如 ImagePullBackOff/CrashLoopBackOff/Readiness 失败）
- **AND** GitOps 工具或控制脚本可检测到发布未达成期望（timeout 或健康状态为 Degraded）

### Requirement: deepAgent 自动诊断与报告
系统 SHALL 在检测到发布失败后触发 deepAgent 诊断，并输出诊断报告与结构化证据。

#### Scenario: Success case
- **WHEN** 发布被判定失败
- **THEN** deepAgent 触发 sandbox_inspector run，并对失败对象执行 focus（至少包含失败 Pod、所属工作负载、相关事件）
- **AND** deepAgent 从 VictoriaLogs 拉取与发布窗口相关的日志片段（应用日志 + kube-system/控制器日志 + Events）
- **AND** 生成 `reports/inspection_report-<thread_id>.md`（或同等命名约定）包含：
  - 失败时间线（发布事件 → 同步 → 异常出现 → 判定失败）
  - 关键症状（Pod 状态、事件摘要、错误码/关键日志）
  - 可能根因（最多 3 条、可证伪）
  - 证据索引（对应 VictoriaLogs 查询语句/过滤条件 + 关键返回片段）
  - 建议动作（含回滚点）

## MODIFIED Requirements

### Requirement: Supervisor 默认诊断流程
现有 Supervisor 提示词与运行约定 SHALL 扩展为：在巡检 JSON/报告生成基础上，附加“发布失败场景”的日志证据拉取与报告章节输出；同时保持不影响非发布场景的默认行为。

## REMOVED Requirements
无

