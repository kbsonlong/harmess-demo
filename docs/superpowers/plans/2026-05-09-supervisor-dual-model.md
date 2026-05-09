# Supervisor Dual-Model + In-Supervisor Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将规划与总结收敛到 Supervisor 主智能体，并支持 Supervisor 使用大模型、子智能体（executor/validator）使用小模型执行与校验。

**Architecture:** Supervisor 负责规划（写 todos）与汇总交付；仅保留 executor/validator 两个子智能体用于取证执行与准出校验。运行时创建两套 LLM，分别注入到 Supervisor 与 subagents。

**Tech Stack:** Python, deepagents + LangGraph, ChatOpenAI (langchain_openai)

---

## File Structure

**Modify**
- `agent_core/config.py`：支持分别读取 SUPERVISOR_* 与 SUBAGENT_* 两套模型配置（key/base/model）
- `agent_core/runtime.py`：create_supervisor_agent 接受两套 LLM；移除 planner 子智能体；subagents 使用 subagent_llm
- `agent_core/profile.py`：移除 planner_prompt 相关字段；保留 workflow_md 注入到 Supervisor prompt
- `agent_core/prompts.py`：默认 Supervisor prompt 改为“Supervisor 规划 + executor 执行 + validator 校验”
- `main.py` / `test.py`：分别创建 supervisor_llm/subagent_llm 并透传到 runtime
- `prompts/default/supervisor.md` / `prompts/testbench/supervisor.md`：移除 planner 角色；明确 Supervisor 规划职责与必须调度 executor/validator
- `profiles/default.json` / `profiles/testbench.json`：移除 planner_prompt_path 与 include_subagents 中的 planner；仅保留 executor/validator
- `tests/test_agent_profile.py`：更新 profile 加载单测
- `CLAUDE.md`：更新架构描述

**Create (if missing)**
- `prompts/default/workflow.md` / `prompts/testbench/workflow.md` 已存在则复用；用于管理员工作流注入 Supervisor prompt

**Delete**
- `prompts/default/planner.md` / `prompts/testbench/planner.md`（不再使用）

---

### Task 1: Add Dual-Model Env Support

**Files:**
- Modify: `agent_core/config.py`
- Test: `tests/test_agent_profile.py` (indirectly)

- [ ] **Step 1: Implement prefixed env reader**

Update `create_llm_from_env()` to accept a `prefix` like `"SUPERVISOR"` or `"SUBAGENT"` and read:
`{PREFIX}_API_KEY / {PREFIX}_API_BASE / {PREFIX}_MODEL`, fallback to legacy `API_KEY/API_BASE/MODEL`.

- [ ] **Step 2: Add two-LLM wiring in entrypoints**

Update `main.py` and `test.py` to construct:
`supervisor_llm = create_llm_from_env(prefix="SUPERVISOR")`
`subagent_llm = create_llm_from_env(prefix="SUBAGENT")`

---

### Task 2: Move Planning Into Supervisor, Remove Planner Subagent

**Files:**
- Modify: `agent_core/runtime.py`
- Modify: `agent_core/prompts.py`
- Modify: `prompts/default/supervisor.md`
- Modify: `prompts/testbench/supervisor.md`
- Delete: `prompts/default/planner.md`
- Delete: `prompts/testbench/planner.md`

- [ ] **Step 1: Update runtime signatures**

Change `create_supervisor_agent()` signature to accept:
`supervisor_llm` and `subagent_llm` (or `llm` + `subagent_llm`), and pass `model=supervisor_llm` to `create_deep_agent`.

Change `create_subagents()` to accept `subagent_llm` and create only:
- `executor` (tools enabled)
- `validator` (no tools)

Ensure `include_subagents` defaults to `["executor","validator"]`.

- [ ] **Step 2: Inject admin workflow into Supervisor prompt**

Reuse existing injection logic (or add equivalent) so that `workflow_md` is appended/replaced into Supervisor system prompt at startup.

- [ ] **Step 3: Update Supervisor prompts**

Remove planner mentions; require:
- Supervisor performs planning itself (based on injected admin workflow)
- Supervisor must call executor/validator and only complete TODO after their Observations
- Never exit while todos are not completed or report not persisted

---

### Task 3: Profile Schema & Prompt/Config Cleanup

**Files:**
- Modify: `agent_core/profile.py`
- Modify: `profiles/default.json`
- Modify: `profiles/testbench.json`
- Modify: `tests/test_agent_profile.py`

- [ ] **Step 1: Remove planner_prompt from AgentProfile**

Drop `planner_prompt` field and loader keys `planner_prompt_path`.

- [ ] **Step 2: Update profiles**

Ensure both profiles reference only:
`supervisor_prompt_path / executor_prompt_path / validator_prompt_path / workflow_md_path`
and set `include_subagents` to `["executor","validator"]` (or omit to use default).

- [ ] **Step 3: Update unit tests**

Update `tests/test_agent_profile.py` JSON fixture and assertions to match new schema.

---

### Task 4: Docs & Final Verification

**Files:**
- Modify: `CLAUDE.md`
- Test: run `./init.sh`

- [ ] **Step 1: Update CLAUDE.md**

Reflect architecture as:
Supervisor handles planning & summarization; executor/validator are subagents; dual model env vars.

- [ ] **Step 2: Run tests**

Run: `./init.sh`
Expected: PASS

---

## Self-Review Checklist

- [ ] No remaining references to `planner` subagent in runtime/profile/profiles
- [ ] Supervisor prompt still enforces “no early exit” and “must call executor/validator”
- [ ] Both model configs can be independently set via SUPERVISOR_* and SUBAGENT_*
- [ ] `./init.sh` passes

