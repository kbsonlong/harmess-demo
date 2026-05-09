# testbench profile（独立测试用）

本项目的默认运行入口是 `main.py`，默认使用 `profiles/default.json` 与 `prompts/` 下的默认 prompts。

为了让 prompts/subagent 设计可以“先独立迭代优化，再同步到主线”，提供一个独立的测试入口：
- profile：`profiles/testbench.json`
- prompts：`prompts/testbench/`
- 入口脚本：`test.py`（默认使用 testbench）

## 如何运行

### 1) 使用 testbench profile（默认）

```bash
uv run python test.py
```

### 2) 指定其它 profile 名称

```bash
AGENT_PROFILE=default uv run python test.py
```

### 3) 指定 profile JSON 的绝对路径

```bash
AGENT_PROFILE_PATH=/abs/path/to/profile.json uv run python test.py
```

## testbench 的设计目标

- 使用三子智能体分工：`planner` + `executor` + `validator`
- Supervisor 只做协调闭环（规划→落盘 TODO→执行取证→校验准出→汇总→报告）
- 输出契约更硬：必须包含编号证据（E1/E2...）/根因假设/修复建议（需人工确认）/验证点，并限制证据体量

## 何时同步到 main

当满足以下条件才建议把 testbench 的 prompts 同步到默认 prompts（main）：

- `uv run --group dev python -m pytest` 全量通过
- 在真实或模拟故障场景下测试结果稳定（能落盘 reports 产物且证据可复现）
- 文档已更新（说明新增/调整的 subagent 责任边界与输出契约）

## 同步步骤（建议）

1. 将 `prompts/testbench/` 中需要固化的内容合并到默认 prompts：
   - `prompts/supervisor.md`
   - `prompts/infra_expert.md`
   - 如需保留 fault_expert 模式：更新 `profiles/default.json` 的 include_subagents 并补齐 `fault_expert_prompt_path`
2. 保持 profiles/default.json 的 subagent 集合与 main.py 行为一致
3. 更新 `docs/` 中的评审/优化记录（可复用模板）
