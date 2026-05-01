# Tasks
- [x] Task 1: 设计并落地 GitOps 发布 Demo 资源
  - [x] 明确 Demo 应用形态（Deployment + Service），定义必需标签（app、release_id 等）与 namespace
  - [x] 提供 GitOps 工具安装与应用同步的最小化清单（默认 Argo CD；以 kind 为基线环境）
  - [x] 提供发布元数据生成与输出（release_id、commit_sha、时间窗），供后续日志检索与报告关联

- [x] Task 2: 部署 VictoriaLogs 与日志采集链路
  - [x] 提供 VictoriaLogs 单实例部署清单（Service/Port/持久化策略可配置）
  - [x] 选择并部署日志采集器（Fluent Bit DaemonSet），实现：
    - [x] 采集容器日志（包含 kube-system）
    - [x] 采集 Kubernetes Events（event-exporter webhook 直推）
    - [x] 采集 GitOps 控制器日志（Argo CD 相关 Pod）
  - [x] 定义日志字段与 stream 约定（namespace/pod/container/app/release_id 等），确保可检索性

- [x] Task 3: 增加发布失败模拟器
  - [x] 支持至少两种失败模式（ImagePullBackOff、CrashLoopBackOff 或 Readiness 失败）
  - [x] 提供触发入口（脚本或 CLI 子命令），并输出“已触发”的 release_id 与目标对象
  - [x] 提供失败判定逻辑（基于 kubectl 状态 + 超时 + GitOps 健康状态）

- [x] Task 4: 扩展 deepAgent 诊断：关联 VictoriaLogs 证据
  - [x] 新增 VictoriaLogs 查询客户端能力（HTTP 请求 + JSONL 解析 + 限制条数/时间窗）
  - [x] 在诊断流程中注入发布上下文（release_id、app、namespace、time range）
  - [x] 诊断时执行：sandbox_inspector run + 对失败对象 focus + VictoriaLogs 多路查询（应用/Events/kube-system/GitOps）
  - [x] 报告生成：在既有报告结构中增加“时间线/日志证据索引/回滚点”章节

- [x] Task 5: 验证与回归
  - [x] 单元测试：VictoriaLogs 查询构造与返回解析（mock HTTP）
  - [x] 集成验证（可选 e2e）：kind 环境中完成一次失败发布 → 触发 deepAgent → 产出 reports/ 报告与证据文件
  - [x] 运行 ./init.sh 并确保通过

- [x] Task 6: 修复并验证 Argo CD 安装在目标 namespace
  - [x] 修复 manifests/gitops/argocd 的 kustomization，使 install.yaml 中的 namespaced 资源实际落在 argocd namespace
  - [x] 更新 gitops_demo.py 的就绪等待逻辑（确保能稳定等待 argocd-server 就绪）
  - [x] kind 集群中执行一次 up（--skip-kind）验证：argocd namespace 下存在部署且可用

- [x] Task 7: 兼容沙箱环境的 kubeconfig 写入
  - [x] kind_demo/gitops_demo 默认使用项目内可写 kubeconfig（如 .demo/kubeconfig），避免写入 ~/.kube
  - [x] 更新相关读取逻辑（kubernetes client 与 kubectl）统一遵循 KUBECONFIG

- [x] Task 8: 修复日志写入字段映射并验证可查询性
  - [x] Fluent Bit 写入 VictoriaLogs 时确保 _msg 字段来自真实日志字段（兼容 cri/parser + kubernetes filter）
  - [x] event-exporter 调整事件时间窗配置，确保可稳定查询到近 10 分钟内事件
  - [x] 在 kind 集群中验证：/select/logsql/query 返回的 _msg 为真实 message，且可按 namespace/container 查询

# Task Dependencies
- Task 2 depends on Task 1（字段/标签约定用于采集与检索）
- Task 3 depends on Task 1
- Task 4 depends on Task 2, Task 3
- Task 5 depends on Task 1, Task 2, Task 3, Task 4
