# AGENTS.md

本仓库是一个 Kubernetes 集群巡检与健康检查系统：用 deepagents + LangGraph 组织多智能体协作，将巡检命令放到集群内的沙箱 Pod 执行，输出结构化 JSON 结果并生成报告。

## Quick Links

- CLAUDE.md
- feature_list.json
- docs/PROGRESS.md
- docs/session-handoff.md
- skills/k8s-inspector/SKILL.md

## Startup Workflow

在写代码/改行为之前：

1. 阅读 CLAUDE.md，确认目标与已有约束
2. 阅读 feature_list.json，只选择一个未完成的 feature（或一个明确的修复点）
3. 运行 ./init.sh（默认跑单测；如需 e2e：RUN_E2E=1 ./init.sh）
4. 如果目标是巡检/排障执行：先 run 再 focus（结构化优先）
5. 开工前确认本次会话结束时要留下的证据与交接信息（见 End of Session）

## Profiles

- 默认 profile：profiles/default.json（引用 prompts/ 下的 *.md）
- 选择 profile：设置环境变量 AGENT_PROFILE=<name>
- 直接指定 profile 文件：设置环境变量 AGENT_PROFILE_PATH=/abs/path/to/profile.json

## Working Rules

- 一次只做一件事：只推进一个 feature 或一个明确的修复点
- 先证据再结论：所有诊断结论必须绑定可复现证据（命令输出摘要、结构化 JSON、关键日志片段）
- 先结构化再自由发挥：优先 sandbox_inspector（run/focus），避免堆 kubectl 原始输出
- 默认只读：不做写操作；任何写操作与权限升级都需要显式确认与隔离的 RBAC
- 完成前必验证：改动后必须通过 ./init.sh，并把证据写进 feature_list.json 的 evidence
- 第三方组件用容器：Redis/MySQL 等尽量用 Docker 运行，避免直接安装到本机

## Verification

- 最小验证：./init.sh
- 仅需跑 e2e 时：RUN_E2E=1 ./init.sh
- 仅需跑巡检脚本时：./run_inspection.sh

## Output Artifacts

- 运行时产物目录：reports/
- 关键文件（约定）
  - reports/todos.json
  - reports/internal_states.json
  - reports/sandbox_inspector-<thread_id>.json（如固化原始巡检 JSON）
  - reports/inspection_report-<thread_id>.md

## End of Session

会话结束前必须完成：

1. 确认 ./init.sh 通过（如跑了 e2e，记录 RUN_E2E=1 的结果）
2. 更新 feature_list.json（只更新本次涉及的 feature：status/evidence）
3. 更新 docs/PROGRESS.md（只记录关键决策、阻塞与下一步）
4. 填写 docs/session-handoff.md（目标、证据、风险、下一步最小可执行）
