# Subagent 优化执行任务与改动记录

日期：2026-04-30

关联评审：[subagent-review-2026-04-30.md](subagent-review-2026-04-30.md)

## 执行任务清单（已完成）

1. 新增专门 subagent：
   - platform_expert：平台组件（Istio/OpenKruise）异常定位
   - access_expert：RBAC/准入 Webhook/策略拒绝异常定位
2. 明确路由规则：Supervisor 将异常按 infra/workload/platform/access 分派，减少重叠与重复采集。
3. 强化输出契约：所有 subagent 统一输出小节结构，强制包含 严重级别/影响面/证据/根因/修复建议。
4. 控制输出体量：为 logs/evidence 增加行数/字符数上限，要求说明截断。
5. 去漂移：将 test.py 重构为复用 agent_core/profile 的同一套 subagent 体系（不再维护另一套 fault_expert）。
6. Profile 扩展：default profile 支持新增 subagent 的 prompt 文件路径；profile loader 保持向后兼容并补充单测。

## 关键改动（代码/文件）

- subagent 运行时统一入口：
  - agent_core/runtime.py：新增 platform_expert / access_expert，并在 create_supervisor_agent 中纳入调度
- profile schema 扩展：
  - agent_core/profile.py：增加 platform_expert_prompt / access_expert_prompt
  - profiles/default.json：增加 platform_expert_prompt_path / access_expert_prompt_path
- prompts（默认）：
  - prompts/platform_expert.md：平台组件专家输出契约与范围
  - prompts/access_expert.md：访问与准入专家输出契约与范围
  - prompts/infra_expert.md、prompts/workload_expert.md：补齐影响面字段与证据截断约束
  - prompts/supervisor.md：更新 subagent 列表与分派规则
- 多入口一致性：
  - test.py：改为复用 agent_core/profile + prompts + runtime（避免两套体系漂移）
- 测试：
  - tests/test_agent_profile.py：覆盖 platform_expert_prompt_path 的加载

## 验收方式

- 单元测试：`uv run --group dev python -m pytest`
- 行为检查：
  - 运行 `uv run python main.py` 后，Supervisor 应能调用四类 subagent，并输出带证据与影响面的诊断结果

