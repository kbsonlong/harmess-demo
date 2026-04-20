# Tasks
- [ ] Task 1: 定义沙箱配置与接口
  - [ ] 定义环境变量与默认值（API_BASE/MODEL/API_KEY 之外，新增 KUBECONFIG、SANDBOX_NAMESPACE、SANDBOX_IMAGE、SANDBOX_TTL_SECONDS、SANDBOX_RBAC_PROFILE、SANDBOX_ALLOW_EXEC）
  - [ ] 定义 Python 接口：create_sandbox / exec_in_sandbox / cleanup_sandbox

- [ ] Task 2: 实现 RBAC 与 Pod manifest 生成
  - [ ] 生成 ServiceAccount、Role、RoleBinding（readonly profile，禁止 secrets）
  - [ ] 生成 Pod（安全上下文默认值、资源限制、禁止 host 相关配置）
  - [ ] 支持 dry-run 生成 YAML（便于审阅）

- [ ] Task 3: 实现 kubectl 适配层
  - [ ] 封装 kubectl apply/delete/wait/exec/logs（参数化调用，避免 shell 注入）
  - [ ] 统一错误处理（区分权限不足/资源不存在/超时）

- [ ] Task 4: 接入 agent 工具
  - [ ] 在 main.py 注册新增工具（k8s sandbox）并保留现有 internet_search
  - [ ] 为工具输出定义清晰的返回结构（JSON 可读）

- [ ] Task 5: 测试与验证
  - [ ] 单元测试：manifest 安全字段与 RBAC 规则（无 secrets 权限、drop ALL、non-root 等）
  - [ ] 本地验证脚本（可选）：对 kind/minikube 运行一次创建→exec→清理的 happy path（需要用户提供集群）

# Task Dependencies
- Task 3 depends on Task 2
- Task 4 depends on Task 1, Task 3
- Task 5 depends on Task 2, Task 3, Task 4
