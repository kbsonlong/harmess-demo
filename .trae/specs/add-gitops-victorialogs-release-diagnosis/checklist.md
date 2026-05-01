- [ ] GitOps 发布 Demo 可在 kind 集群中安装并完成一次成功同步（应用达到 Ready/Available）
- [ ] VictoriaLogs 可被访问（集群内 Service 可用；必要时支持 port-forward）并可写入/查询日志
- [ ] 日志采集链路覆盖：容器日志（含 kube-system）、Kubernetes Events、GitOps 控制器日志
- [ ] 发布失败模拟器可稳定复现至少两种失败模式，并输出可关联的 release_id 与时间窗
- [ ] 发布失败可被判定（健康状态/超时/关键事件），并自动触发 deepAgent 诊断流程
- [ ] 诊断报告落盘到 reports/，包含：时间线、关键症状、最多 3 条可证伪根因、证据索引（含 VictoriaLogs 查询条件与片段）、建议动作与回滚点
- [ ] 结构化证据文件落盘（至少包含：sandbox_inspector JSON、VictoriaLogs 查询结果截取或摘要）
- [ ] ./init.sh 通过（如启用 e2e，记录 RUN_E2E=1 的结果）

