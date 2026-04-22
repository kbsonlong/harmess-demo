from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .types import Evidence, FocusRef, Severity
from .utils import env_int, env_str, json_dumps, stable_id, truncate_lines, truncate_text, utc_now_iso


@dataclass(frozen=True)
class InspectorConfig:
    """巡检器配置：用于限制采集范围与输出规模，避免 Token 爆炸。"""

    time_window_seconds: int = 7200
    max_findings: int = 50
    max_items_scanned: int = 2000
    max_name_list: int = 20
    evidence_max_chars: int = 800
    evidence_max_lines: int = 40
    evidence_max_chars_per_line: int = 300
    log_tail_lines: int = 200
    log_max_chars: int = 6000
    per_type_limit: int = 20


def _load_config() -> None:
    """加载 Kubernetes 访问配置：优先 kubeconfig，失败则回退到 in-cluster 配置。"""
    kubeconfig = os.getenv("KUBECONFIG")
    try:
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
            config.load_kube_config()
        return
    except Exception:
        config.load_incluster_config()


def _read_file(path: str) -> Optional[str]:
    """读取文本文件并 strip；失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _sa_namespace() -> Optional[str]:
    """读取当前 Pod 的 ServiceAccount namespace（in-cluster 方式）。"""
    return _read_file("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


def _api_exc_summary(e: ApiException) -> dict[str, Any]:
    """将 Kubernetes ApiException 转为可读的精简摘要。"""
    return {"status": getattr(e, "status", None), "reason": getattr(e, "reason", None), "body": truncate_text(getattr(e, "body", None), max_chars=400)}


def _is_permission_denied(e: Exception) -> bool:
    """判断异常是否属于 RBAC/鉴权拒绝（401/403 或类似文本）。"""
    if isinstance(e, ApiException):
        return e.status in {401, 403}
    msg = str(e).lower()
    return any(x in msg for x in ("forbidden", "unauthorized"))


def _pagination_loop(
    *,
    list_func: Callable[..., Any],
    limit: int,
    max_items: int,
    **kwargs: Any,
) -> list[Any]:
    """对 list API 做分页聚合，同时对最大条数做硬限制。"""
    out: list[Any] = []
    cont: Optional[str] = None
    while True:
        resp = list_func(limit=limit, _continue=cont, **kwargs)
        items = list(getattr(resp, "items", []) or [])
        out.extend(items)
        cont = getattr(getattr(resp, "metadata", None), "_continue", None) or getattr(getattr(resp, "metadata", None), "continue_", None)
        if len(out) >= max_items:
            return out[:max_items]
        if not cont:
            return out


class Inspector:
    """Kubernetes 巡检执行器：负责 run（汇总巡检）与 focus（单对象深挖）。"""

    def __init__(self, config: Optional[InspectorConfig] = None):
        """初始化巡检器并创建各类 K8s API Client。"""
        self.config = config or InspectorConfig()
        _load_config()
        self.api_client = client.ApiClient()
        self.core = client.CoreV1Api(self.api_client)
        self.apps = client.AppsV1Api(self.api_client)
        self.batch = client.BatchV1Api(self.api_client)
        self.storage = client.StorageV1Api(self.api_client)
        self.authz = client.AuthorizationV1Api(self.api_client)
        self.version = client.VersionApi(self.api_client)

    def _can(self, *, group: str, resource: str, verb: str, namespace: Optional[str] = None) -> dict[str, Any]:
        """使用 SelfSubjectAccessReview 探测当前身份对资源/动词是否有权限。"""
        spec = client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=client.V1ResourceAttributes(group=group or "", resource=resource, verb=verb, namespace=namespace)
        )
        sar = client.V1SelfSubjectAccessReview(spec=spec)
        try:
            resp = self.authz.create_self_subject_access_review(body=sar)
            status = getattr(resp, "status", None)
            allowed = bool(getattr(status, "allowed", False))
            reason = getattr(status, "reason", None)
            out = {"group": group, "resource": resource, "verb": verb, "namespace": namespace, "allowed": allowed}
            if not allowed:
                out["reason"] = reason
            return out
        except Exception as e:
            return {
                "group": group,
                "resource": resource,
                "verb": verb,
                "namespace": namespace,
                "allowed": False,
                "reason": "error",
                "error": truncate_text(str(e), max_chars=300),
                "permission_denied": _is_permission_denied(e),
            }

    def _context(self) -> dict[str, Any]:
        """采集巡检执行上下文（namespace/pod/serviceAccount/k8s 版本等）。"""
        ns = _sa_namespace() or env_str("SANDBOX_NAMESPACE", "")
        pod = env_str("HOSTNAME", "")
        sa = env_str("SANDBOX_SERVICEACCOUNT_NAME", "")
        if not sa and ns and pod:
            try:
                pod_obj = self.core.read_namespaced_pod(name=pod, namespace=ns)
                sa = getattr(getattr(pod_obj, "spec", None), "service_account_name", None) or ""
            except Exception:
                sa = ""
        if not sa:
            sa = "unknown"
        version: dict[str, Any] = {}
        try:
            code = self.version.get_code()
            version = self.api_client.sanitize_for_serialization(code)
        except Exception as e:
            version = {"error": truncate_text(str(e), max_chars=200), "permission_denied": _is_permission_denied(e)}
        return {"namespace": ns or None, "pod_name": pod or None, "service_account": sa, "kubernetes_version": version}

    def run(self) -> dict[str, Any]:
        """执行全量巡检并输出结构化 JSON：summary + findings + permissions + stats。"""
        cfg = self.config
        now = utc_now_iso()
        context = self._context()
        permissions = self._probe_permissions(context.get("namespace"))

        findings: list[dict[str, Any]] = []
        stats: dict[str, Any] = {"scanned": {}, "truncated": {}}

        def add_finding(f: dict[str, Any]) -> None:
            """追加一条异常；超过最大数量时标记截断并停止增长。"""
            if len(findings) >= cfg.max_findings:
                stats["truncated"]["max_findings_reached"] = True
                return
            findings.append(f)

        try:
            for f in self._check_nodes(permissions, stats):
                add_finding(f)
            for f in self._check_kube_system(permissions, stats):
                add_finding(f)
            for f in self._check_workloads(permissions, stats):
                add_finding(f)
            for f in self._check_pods(permissions, stats):
                add_finding(f)
            for f in self._check_storage(permissions, stats):
                add_finding(f)
            for f in self._check_quota_limits(permissions, stats):
                add_finding(f)
        except Exception as e:
            add_finding(
                {
                    "id": stable_id("internal_error", str(e)),
                    "severity": "P1",
                    "type": "inspector_internal_error",
                    "title": "Inspector internal error",
                    "symptom": truncate_text(str(e), max_chars=300),
                    "evidence": [
                        {"kind": "traceback", "ref": None, "message": truncate_lines(traceback.format_exc(), max_lines=30, max_chars_per_line=200)}
                    ],
                    "focus_refs": [],
                }
            )

        summary = self._summarize(findings)
        return {
            "schema_version": "1",
            "generated_at": now,
            "context": context,
            "permissions": permissions,
            "summary": summary,
            "findings": findings,
            "stats": stats,
        }

    def focus(
        self,
        *,
        ref: FocusRef,
        include_logs: bool = True,
        include_events: bool = True,
    ) -> dict[str, Any]:
        """针对单个对象做聚焦采集（事件/日志/对象详情），用于逐条异常深挖。"""
        cfg = self.config
        now = utc_now_iso()
        kind = (ref.kind or "").lower()
        out: dict[str, Any] = {"schema_version": "1", "generated_at": now, "ref": ref.to_dict(), "object": None, "events": [], "logs": []}

        if include_events:
            out["events"] = self._focus_events(ref, max_events=cfg.per_type_limit)

        if kind == "pod":
            out["object"] = self._get_pod(ref)
            if include_logs:
                out["logs"] = self._focus_pod_logs(ref)
            return out

        if kind == "node":
            out["object"] = self._get_node(ref)
            return out

        if kind in {"deployment", "statefulset", "daemonset"}:
            out["object"] = self._get_workload(kind, ref)
            return out

        if kind == "job":
            out["object"] = self._get_job(ref)
            return out

        out["object"] = {"error": "unsupported_kind", "kind": ref.kind}
        return out

    def _probe_permissions(self, default_ns: Optional[str]) -> dict[str, Any]:
        """抽样探测巡检所需的最小只读权限集合，并汇总缺失项。"""
        checks = [
            self._can(group="", resource="pods", verb="list", namespace=default_ns),
            self._can(group="", resource="pods", verb="get", namespace=default_ns),
            self._can(group="", resource="pods/log", verb="get", namespace=default_ns),
            self._can(group="", resource="events", verb="list", namespace=default_ns),
            self._can(group="", resource="nodes", verb="list", namespace=None),
            self._can(group="apps", resource="deployments", verb="list", namespace=None),
            self._can(group="apps", resource="statefulsets", verb="list", namespace=None),
            self._can(group="apps", resource="daemonsets", verb="list", namespace=None),
            self._can(group="apps", resource="replicasets", verb="list", namespace=None),
            self._can(group="batch", resource="jobs", verb="list", namespace=None),
            self._can(group="storage.k8s.io", resource="storageclasses", verb="list", namespace=None),
            self._can(group="", resource="persistentvolumeclaims", verb="list", namespace=None),
            self._can(group="", resource="persistentvolumes", verb="list", namespace=None),
            self._can(group="", resource="resourcequotas", verb="list", namespace=None),
            self._can(group="", resource="limitranges", verb="list", namespace=None),
        ]
        missing = [c for c in checks if not c.get("allowed")]
        return {"checks": checks, "missing": missing}

    def _summarize(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        """根据 findings 的严重度汇总结论与 Top 发现。"""
        counts = {"P0": 0, "P1": 0, "P2": 0}
        for f in findings:
            sev = f.get("severity")
            if sev in counts:
                counts[sev] += 1
        conclusion = "healthy"
        if counts["P0"] > 0:
            conclusion = "outage"
        elif counts["P1"] > 0:
            conclusion = "risk"
        top = [f.get("id") for f in findings[:3] if f.get("id")]
        return {"conclusion": conclusion, "counts": counts, "top_findings": top}

    def _check_nodes(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查节点健康：NotReady、Pressure、NetworkUnavailable。"""
        cfg = self.config
        if not self._allowed(permissions, group="", resource="nodes", verb="list"):
            return [self._missing_perm_finding("nodes/list")]
        nodes = _pagination_loop(list_func=self.core.list_node, limit=200, max_items=cfg.max_items_scanned)
        stats["scanned"]["nodes"] = len(nodes)
        not_ready: list[dict[str, Any]] = []
        pressure: list[dict[str, Any]] = []
        net_unavail: list[dict[str, Any]] = []
        for n in nodes:
            nd = self.api_client.sanitize_for_serialization(n)
            name = (nd.get("metadata") or {}).get("name")
            conds = (nd.get("status") or {}).get("conditions") or []
            by_type = {c.get("type"): c for c in conds if c.get("type")}
            ready = by_type.get("Ready")
            if ready and ready.get("status") != "True":
                not_ready.append({"name": name, "reason": ready.get("reason"), "message": truncate_text(ready.get("message"), max_chars=200)})
            for t in ("DiskPressure", "MemoryPressure", "PIDPressure"):
                c = by_type.get(t)
                if c and c.get("status") == "True":
                    pressure.append({"name": name, "type": t, "reason": c.get("reason"), "message": truncate_text(c.get("message"), max_chars=200)})
            nu = by_type.get("NetworkUnavailable")
            if nu and nu.get("status") == "True":
                net_unavail.append({"name": name, "reason": nu.get("reason"), "message": truncate_text(nu.get("message"), max_chars=200)})

        findings: list[dict[str, Any]] = []
        if not_ready:
            items = not_ready[: cfg.max_name_list]
            fid = stable_id("node_not_ready", json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": "P0",
                    "type": "node_not_ready",
                    "title": "Node NotReady",
                    "symptom": f"发现 {len(not_ready)} 个节点 NotReady",
                    "evidence": [Evidence(kind="node", ref={"nodes": items}, message="节点 Ready 条件非 True").to_dict()],
                    "focus_refs": [FocusRef(kind="Node", name=items[0]["name"]).to_dict()] if items and items[0].get("name") else [],
                }
            )
        if pressure:
            items = pressure[: cfg.max_name_list]
            fid = stable_id("node_pressure", json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": "P1",
                    "type": "node_pressure",
                    "title": "Node Pressure",
                    "symptom": f"发现 {len(pressure)} 条节点 Pressure 条件为 True",
                    "evidence": [Evidence(kind="node", ref={"pressure": items}, message="节点压力条件异常").to_dict()],
                    "focus_refs": [FocusRef(kind="Node", name=items[0]["name"]).to_dict()] if items and items[0].get("name") else [],
                }
            )
        if net_unavail:
            items = net_unavail[: cfg.max_name_list]
            fid = stable_id("node_network_unavailable", json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": "P0",
                    "type": "node_network_unavailable",
                    "title": "Node NetworkUnavailable",
                    "symptom": f"发现 {len(net_unavail)} 个节点 NetworkUnavailable",
                    "evidence": [Evidence(kind="node", ref={"nodes": items}, message="节点 NetworkUnavailable=True").to_dict()],
                    "focus_refs": [FocusRef(kind="Node", name=items[0]["name"]).to_dict()] if items and items[0].get("name") else [],
                }
            )
        return findings

    def _check_kube_system(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查 kube-system 核心组件：筛选非 Running/Succeeded 的 Pod。"""
        cfg = self.config
        if not self._allowed(permissions, group="", resource="pods", verb="list"):
            return [self._missing_perm_finding("pods/list")]
        try:
            pods = _pagination_loop(
                list_func=self.core.list_namespaced_pod,
                namespace="kube-system",
                field_selector="status.phase!=Running,status.phase!=Succeeded",
                limit=200,
                max_items=cfg.max_items_scanned,
            )
        except Exception as e:
            if _is_permission_denied(e):
                return [self._missing_perm_finding("pods/list kube-system", error=e)]
            raise
        stats["scanned"]["kube_system_abnormal_pods"] = len(pods)
        if not pods:
            return []
        items: list[dict[str, Any]] = []
        for p in pods[: cfg.max_name_list]:
            pd = self.api_client.sanitize_for_serialization(p)
            meta = pd.get("metadata") or {}
            st = pd.get("status") or {}
            items.append(
                {
                    "namespace": meta.get("namespace"),
                    "name": meta.get("name"),
                    "phase": st.get("phase"),
                    "reason": st.get("reason"),
                    "message": truncate_text(st.get("message"), max_chars=200),
                }
            )
        fid = stable_id("kube_system_pods_not_running", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P0",
                "type": "kube_system_pods_not_running",
                "title": "kube-system 异常 Pod",
                "symptom": f"kube-system 存在 {len(pods)} 个 Pod 非 Running/Succeeded",
                "evidence": [Evidence(kind="pod", ref={"pods": items}, message="kube-system Pod 异常相位").to_dict()],
                "focus_refs": [
                    FocusRef(kind="Pod", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()
                ]
                if items and items[0].get("name")
                else [],
            }
        ]

    def _check_workloads(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查工作负载健康：Deployment/StatefulSet/DaemonSet/ReplicaSet/Job。"""
        cfg = self.config
        findings: list[dict[str, Any]] = []
        if self._allowed(permissions, group="apps", resource="deployments", verb="list"):
            findings.extend(self._check_deployments(stats))
        else:
            findings.append(self._missing_perm_finding("apps/deployments list"))
        if self._allowed(permissions, group="apps", resource="statefulsets", verb="list"):
            findings.extend(self._check_statefulsets(stats))
        else:
            findings.append(self._missing_perm_finding("apps/statefulsets list"))
        if self._allowed(permissions, group="apps", resource="daemonsets", verb="list"):
            findings.extend(self._check_daemonsets(stats))
        else:
            findings.append(self._missing_perm_finding("apps/daemonsets list"))
        if self._allowed(permissions, group="apps", resource="replicasets", verb="list"):
            findings.extend(self._check_replicasets(stats))
        else:
            findings.append(self._missing_perm_finding("apps/replicasets list"))
        if self._allowed(permissions, group="batch", resource="jobs", verb="list"):
            findings.extend(self._check_jobs(stats))
        else:
            findings.append(self._missing_perm_finding("batch/jobs list"))
        return [f for f in findings if f.get("type") != "missing_permission" or f.get("missing", {}).get("resource") not in {None}]

    def _check_deployments(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        """筛选 available != desired 的 Deployment。"""
        cfg = self.config
        deps = _pagination_loop(list_func=self.apps.list_deployment_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
        stats["scanned"]["deployments"] = len(deps)
        bad: list[dict[str, Any]] = []
        for d in deps:
            dd = self.api_client.sanitize_for_serialization(d)
            spec = dd.get("spec") or {}
            st = dd.get("status") or {}
            desired = spec.get("replicas") or 0
            available = st.get("availableReplicas") or 0
            if desired > 0 and available != desired:
                meta = dd.get("metadata") or {}
                bad.append(
                    {
                        "namespace": meta.get("namespace"),
                        "name": meta.get("name"),
                        "desired": desired,
                        "available": available,
                    }
                )
        if not bad:
            return []
        items = bad[: cfg.max_name_list]
        fid = stable_id("deployment_unavailable", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P1",
                "type": "deployment_unavailable",
                "title": "Deployment 副本不达标",
                "symptom": f"发现 {len(bad)} 个 Deployment 的 available != desired",
                "evidence": [Evidence(kind="deployment", ref={"deployments": items}, message="Deployment availableReplicas 不达标").to_dict()],
                "focus_refs": [
                    FocusRef(kind="Deployment", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()
                ]
                if items and items[0].get("name")
                else [],
            }
        ]

    def _check_statefulsets(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        """筛选 ready != desired 的 StatefulSet。"""
        cfg = self.config
        sts = _pagination_loop(list_func=self.apps.list_stateful_set_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
        stats["scanned"]["statefulsets"] = len(sts)
        bad: list[dict[str, Any]] = []
        for s in sts:
            sd = self.api_client.sanitize_for_serialization(s)
            spec = sd.get("spec") or {}
            st = sd.get("status") or {}
            desired = spec.get("replicas") or 0
            ready = st.get("readyReplicas") or 0
            if desired > 0 and ready != desired:
                meta = sd.get("metadata") or {}
                bad.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "desired": desired, "ready": ready})
        if not bad:
            return []
        items = bad[: cfg.max_name_list]
        fid = stable_id("statefulset_unready", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P1",
                "type": "statefulset_unready",
                "title": "StatefulSet 副本不达标",
                "symptom": f"发现 {len(bad)} 个 StatefulSet 的 ready != desired",
                "evidence": [Evidence(kind="statefulset", ref={"statefulsets": items}, message="StatefulSet readyReplicas 不达标").to_dict()],
                "focus_refs": [
                    FocusRef(kind="StatefulSet", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()
                ]
                if items and items[0].get("name")
                else [],
            }
        ]

    def _check_daemonsets(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        """筛选 available != desired 的 DaemonSet。"""
        cfg = self.config
        dss = _pagination_loop(list_func=self.apps.list_daemon_set_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
        stats["scanned"]["daemonsets"] = len(dss)
        bad: list[dict[str, Any]] = []
        for d in dss:
            dd = self.api_client.sanitize_for_serialization(d)
            st = dd.get("status") or {}
            desired = st.get("desiredNumberScheduled") or 0
            available = st.get("numberAvailable") or 0
            if desired > 0 and available != desired:
                meta = dd.get("metadata") or {}
                bad.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "desired": desired, "available": available})
        if not bad:
            return []
        items = bad[: cfg.max_name_list]
        fid = stable_id("daemonset_unavailable", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P1",
                "type": "daemonset_unavailable",
                "title": "DaemonSet 副本不达标",
                "symptom": f"发现 {len(bad)} 个 DaemonSet 的 available != desired",
                "evidence": [Evidence(kind="daemonset", ref={"daemonsets": items}, message="DaemonSet numberAvailable 不达标").to_dict()],
                "focus_refs": [
                    FocusRef(kind="DaemonSet", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()
                ]
                if items and items[0].get("name")
                else [],
            }
        ]

    def _check_replicasets(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        """筛选 ready != desired 的 ReplicaSet（通常作为辅助信号）。"""
        cfg = self.config
        rss = _pagination_loop(list_func=self.apps.list_replica_set_for_all_namespaces, limit=200, max_items=min(cfg.max_items_scanned, 1000))
        stats["scanned"]["replicasets"] = len(rss)
        bad: list[dict[str, Any]] = []
        for r in rss:
            rd = self.api_client.sanitize_for_serialization(r)
            spec = rd.get("spec") or {}
            st = rd.get("status") or {}
            desired = spec.get("replicas") or 0
            ready = st.get("readyReplicas") or 0
            if desired > 0 and ready != desired:
                meta = rd.get("metadata") or {}
                bad.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "desired": desired, "ready": ready})
        if not bad:
            return []
        items = bad[: cfg.max_name_list]
        fid = stable_id("replicaset_unready", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P2",
                "type": "replicaset_unready",
                "title": "ReplicaSet 副本不达标",
                "symptom": f"发现 {len(bad)} 个 ReplicaSet 的 ready != desired",
                "evidence": [Evidence(kind="replicaset", ref={"replicasets": items}, message="ReplicaSet readyReplicas 不达标").to_dict()],
                "focus_refs": [],
            }
        ]

    def _check_jobs(self, stats: dict[str, Any]) -> list[dict[str, Any]]:
        """筛选 failed > 0 的 Job。"""
        cfg = self.config
        jobs = _pagination_loop(list_func=self.batch.list_job_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
        stats["scanned"]["jobs"] = len(jobs)
        bad: list[dict[str, Any]] = []
        for j in jobs:
            jd = self.api_client.sanitize_for_serialization(j)
            st = jd.get("status") or {}
            failed = st.get("failed") or 0
            if failed and failed > 0:
                meta = jd.get("metadata") or {}
                bad.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "failed": failed})
        if not bad:
            return []
        items = bad[: cfg.max_name_list]
        fid = stable_id("job_failed", json_dumps(items))
        return [
            {
                "id": fid,
                "severity": "P2",
                "type": "job_failed",
                "title": "Job 失败",
                "symptom": f"发现 {len(bad)} 个 Job 存在 failed > 0",
                "evidence": [Evidence(kind="job", ref={"jobs": items}, message="Job failed 计数非零").to_dict()],
                "focus_refs": [FocusRef(kind="Job", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()] if items and items[0].get("name") else [],
            }
        ]

    def _check_pods(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查异常 Pod：非 Running/Succeeded，并按常见原因聚合。"""
        cfg = self.config
        if not self._allowed(permissions, group="", resource="pods", verb="list"):
            return [self._missing_perm_finding("pods/list")]
        pods = _pagination_loop(
            list_func=self.core.list_pod_for_all_namespaces,
            field_selector="status.phase!=Running,status.phase!=Succeeded",
            limit=200,
            max_items=min(cfg.max_items_scanned, 1500),
        )
        stats["scanned"]["abnormal_pods"] = len(pods)
        if not pods:
            return []
        by_reason: dict[str, list[dict[str, Any]]] = {}
        for p in pods:
            pd = self.api_client.sanitize_for_serialization(p)
            meta = pd.get("metadata") or {}
            st = pd.get("status") or {}
            ns = meta.get("namespace")
            name = meta.get("name")
            phase = st.get("phase")
            reason = st.get("reason") or phase or "Unknown"
            cs = (st.get("containerStatuses") or []) + (st.get("initContainerStatuses") or [])
            derived = self._pod_reason(cs) or reason
            by_reason.setdefault(derived, []).append(
                {
                    "namespace": ns,
                    "name": name,
                    "phase": phase,
                    "reason": derived,
                }
            )
        findings: list[dict[str, Any]] = []
        for reason, items_all in sorted(by_reason.items(), key=lambda x: len(x[1]), reverse=True):
            items = items_all[: cfg.max_name_list]
            sev: Severity = "P1"
            if reason in {"ErrImagePull", "ImagePullBackOff"}:
                sev = "P1"
            elif reason in {"CrashLoopBackOff", "OOMKilled"}:
                sev = "P1"
            elif reason in {"Pending"}:
                sev = "P2"
            fid = stable_id("pod_abnormal", reason, json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": sev,
                    "type": "pod_abnormal",
                    "title": f"异常 Pod：{reason}",
                    "symptom": f"发现 {len(items_all)} 个 Pod 异常（{reason}）",
                    "evidence": [Evidence(kind="pod", ref={"reason": reason, "pods": items}, message="Pod 相位/容器状态异常").to_dict()],
                    "focus_refs": [FocusRef(kind="Pod", namespace=items[0].get("namespace"), name=items[0].get("name")).to_dict()]
                    if items and items[0].get("name")
                    else [],
                }
            )
            if len(findings) >= cfg.per_type_limit:
                break
        return findings

    def _pod_reason(self, statuses: list[dict[str, Any]]) -> Optional[str]:
        """从容器状态推导更具体的异常原因（如 ImagePullBackOff/OOMKilled）。"""
        for cs in statuses:
            state = (cs.get("state") or {})
            waiting = state.get("waiting") or {}
            reason = waiting.get("reason")
            if reason:
                if reason in {"ErrImagePull", "ImagePullBackOff", "CrashLoopBackOff", "CreateContainerConfigError"}:
                    return reason
            terminated = state.get("terminated") or {}
            if terminated.get("reason") == "OOMKilled":
                return "OOMKilled"
        return None

    def _check_storage(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查存储：StorageClass、PVC/PV 非 Bound。"""
        cfg = self.config
        findings: list[dict[str, Any]] = []
        if self._allowed(permissions, group="storage.k8s.io", resource="storageclasses", verb="list"):
            try:
                sc = _pagination_loop(list_func=self.storage.list_storage_class, limit=200, max_items=200)
                stats["scanned"]["storageclasses"] = len(sc)
            except Exception as e:
                if _is_permission_denied(e):
                    findings.append(self._missing_perm_finding("storageclasses/list", error=e))
        else:
            findings.append(self._missing_perm_finding("storageclasses/list"))

        pvc_pending: list[dict[str, Any]] = []
        if self._allowed(permissions, group="", resource="persistentvolumeclaims", verb="list"):
            pvcs = _pagination_loop(list_func=self.core.list_persistent_volume_claim_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
            stats["scanned"]["pvcs"] = len(pvcs)
            for p in pvcs:
                d = self.api_client.sanitize_for_serialization(p)
                st = d.get("status") or {}
                if st.get("phase") != "Bound":
                    meta = d.get("metadata") or {}
                    pvc_pending.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "phase": st.get("phase")})
        else:
            findings.append(self._missing_perm_finding("persistentvolumeclaims/list"))

        pv_not_bound: list[dict[str, Any]] = []
        if self._allowed(permissions, group="", resource="persistentvolumes", verb="list"):
            pvs = _pagination_loop(list_func=self.core.list_persistent_volume, limit=200, max_items=cfg.max_items_scanned)
            stats["scanned"]["pvs"] = len(pvs)
            for p in pvs:
                d = self.api_client.sanitize_for_serialization(p)
                st = d.get("status") or {}
                phase = st.get("phase")
                if phase != "Bound":
                    meta = d.get("metadata") or {}
                    pv_not_bound.append({"name": meta.get("name"), "phase": phase})
        else:
            findings.append(self._missing_perm_finding("persistentvolumes/list"))

        if pvc_pending:
            items = pvc_pending[: cfg.max_name_list]
            fid = stable_id("pvc_not_bound", json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": "P1",
                    "type": "pvc_not_bound",
                    "title": "PVC 非 Bound",
                    "symptom": f"发现 {len(pvc_pending)} 个 PVC 非 Bound",
                    "evidence": [Evidence(kind="pvc", ref={"pvcs": items}, message="PVC phase != Bound").to_dict()],
                    "focus_refs": [],
                }
            )
        if pv_not_bound:
            items = pv_not_bound[: cfg.max_name_list]
            fid = stable_id("pv_not_bound", json_dumps(items))
            findings.append(
                {
                    "id": fid,
                    "severity": "P2",
                    "type": "pv_not_bound",
                    "title": "PV 非 Bound",
                    "symptom": f"发现 {len(pv_not_bound)} 个 PV 非 Bound",
                    "evidence": [Evidence(kind="pv", ref={"pvs": items}, message="PV phase != Bound").to_dict()],
                    "focus_refs": [],
                }
            )
        return findings

    def _check_quota_limits(self, permissions: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        """检查资源配额与限制：ResourceQuota/LimitRange（输出摘要）。"""
        cfg = self.config
        findings: list[dict[str, Any]] = []
        if self._allowed(permissions, group="", resource="resourcequotas", verb="list"):
            rqs = _pagination_loop(list_func=self.core.list_resource_quota_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
            stats["scanned"]["resourcequotas"] = len(rqs)
            if rqs:
                items: list[dict[str, Any]] = []
                for rq in rqs[: cfg.max_name_list]:
                    d = self.api_client.sanitize_for_serialization(rq)
                    meta = d.get("metadata") or {}
                    st = d.get("status") or {}
                    items.append({"namespace": meta.get("namespace"), "name": meta.get("name"), "used": st.get("used"), "hard": st.get("hard")})
                fid = stable_id("resourcequota_present", json_dumps(items))
                findings.append(
                    {
                        "id": fid,
                        "severity": "P2",
                        "type": "resourcequota_present",
                        "title": "ResourceQuota 存在（摘要）",
                        "symptom": f"集群存在 {len(rqs)} 个 ResourceQuota（仅输出摘要）",
                        "evidence": [Evidence(kind="resourcequota", ref={"resourcequotas": items}, message="ResourceQuota used/hard 摘要").to_dict()],
                        "focus_refs": [],
                    }
                )
        else:
            findings.append(self._missing_perm_finding("resourcequotas/list"))

        if self._allowed(permissions, group="", resource="limitranges", verb="list"):
            lrs = _pagination_loop(list_func=self.core.list_limit_range_for_all_namespaces, limit=200, max_items=cfg.max_items_scanned)
            stats["scanned"]["limitranges"] = len(lrs)
            if lrs:
                items: list[dict[str, Any]] = []
                for lr in lrs[: cfg.max_name_list]:
                    d = self.api_client.sanitize_for_serialization(lr)
                    meta = d.get("metadata") or {}
                    items.append({"namespace": meta.get("namespace"), "name": meta.get("name")})
                fid = stable_id("limitrange_present", json_dumps(items))
                findings.append(
                    {
                        "id": fid,
                        "severity": "P2",
                        "type": "limitrange_present",
                        "title": "LimitRange 存在（摘要）",
                        "symptom": f"集群存在 {len(lrs)} 个 LimitRange（仅输出名称）",
                        "evidence": [Evidence(kind="limitrange", ref={"limitranges": items}, message="LimitRange 名称摘要").to_dict()],
                        "focus_refs": [],
                    }
                )
        else:
            findings.append(self._missing_perm_finding("limitranges/list"))

        return findings

    def _focus_events(self, ref: FocusRef, *, max_events: int) -> list[dict[str, Any]]:
        """按对象引用筛选相关 Events（优先 namespace 内，必要时全局）。"""
        cfg = self.config
        ns = ref.namespace
        kind = ref.kind
        name = ref.name
        kind_lower = (kind or "").lower()
        kind_map = {
            "pod": "Pod",
            "node": "Node",
            "deployment": "Deployment",
            "statefulset": "StatefulSet",
            "daemonset": "DaemonSet",
            "job": "Job",
        }
        event_kind = kind_map.get(kind_lower)
        parts = [f"involvedObject.name={name}"]
        if event_kind:
            parts.append(f"involvedObject.kind={event_kind}")
        field_selector = ",".join(parts)
        try:
            if ns:
                evs = _pagination_loop(
                    list_func=self.core.list_namespaced_event,
                    namespace=ns,
                    field_selector=field_selector,
                    limit=200,
                    max_items=max_events,
                )
            else:
                evs = _pagination_loop(
                    list_func=self.core.list_event_for_all_namespaces,
                    field_selector=field_selector,
                    limit=200,
                    max_items=max_events,
                )
        except Exception as e:
            return [{"error": truncate_text(str(e), max_chars=300), "permission_denied": _is_permission_denied(e)}]
        out: list[dict[str, Any]] = []
        for ev in evs[:max_events]:
            d = self.api_client.sanitize_for_serialization(ev)
            out.append(
                {
                    "type": d.get("type"),
                    "reason": d.get("reason"),
                    "message": truncate_text(d.get("message"), max_chars=cfg.evidence_max_chars),
                    "count": d.get("count"),
                    "lastTimestamp": d.get("lastTimestamp") or d.get("eventTime") or d.get("metadata", {}).get("creationTimestamp"),
                    "involvedObject": d.get("involvedObject"),
                }
            )
        return out

    def _focus_pod_logs(self, ref: FocusRef) -> list[dict[str, Any]]:
        """采集 Pod 日志（当前与 previous），并对长度做强截断。"""
        cfg = self.config
        if not ref.namespace:
            return [{"error": "pod namespace required"}]
        container = ref.container
        res: list[dict[str, Any]] = []
        for previous in (False, True):
            try:
                text = self.core.read_namespaced_pod_log(
                    name=ref.name,
                    namespace=ref.namespace,
                    container=container,
                    tail_lines=cfg.log_tail_lines,
                    timestamps=True,
                    previous=previous,
                )
                res.append(
                    {
                        "previous": previous,
                        "container": container,
                        "log": truncate_text(text, max_chars=cfg.log_max_chars),
                    }
                )
            except Exception as e:
                msg = truncate_text(str(e), max_chars=300)
                if previous:
                    continue
                res.append({"previous": previous, "container": container, "error": msg, "permission_denied": _is_permission_denied(e)})
        return res

    def _get_pod(self, ref: FocusRef) -> dict[str, Any]:
        """读取 Pod 对象详情（去除 managedFields 以减小体积）。"""
        if not ref.namespace:
            return {"error": "pod namespace required"}
        try:
            pod = self.core.read_namespaced_pod(name=ref.name, namespace=ref.namespace)
            d = self.api_client.sanitize_for_serialization(pod)
            d.pop("managedFields", None)
            return d
        except Exception as e:
            return {"error": truncate_text(str(e), max_chars=300), "permission_denied": _is_permission_denied(e)}

    def _get_node(self, ref: FocusRef) -> dict[str, Any]:
        """读取 Node 对象详情（去除 managedFields 以减小体积）。"""
        try:
            n = self.core.read_node(name=ref.name)
            d = self.api_client.sanitize_for_serialization(n)
            d.pop("managedFields", None)
            return d
        except Exception as e:
            return {"error": truncate_text(str(e), max_chars=300), "permission_denied": _is_permission_denied(e)}

    def _get_workload(self, kind: str, ref: FocusRef) -> dict[str, Any]:
        """读取工作负载对象详情（Deployment/StatefulSet/DaemonSet）。"""
        if not ref.namespace:
            return {"error": "workload namespace required"}
        try:
            if kind == "deployment":
                obj = self.apps.read_namespaced_deployment(name=ref.name, namespace=ref.namespace)
            elif kind == "statefulset":
                obj = self.apps.read_namespaced_stateful_set(name=ref.name, namespace=ref.namespace)
            else:
                obj = self.apps.read_namespaced_daemon_set(name=ref.name, namespace=ref.namespace)
            d = self.api_client.sanitize_for_serialization(obj)
            d.pop("managedFields", None)
            return d
        except Exception as e:
            return {"error": truncate_text(str(e), max_chars=300), "permission_denied": _is_permission_denied(e)}

    def _get_job(self, ref: FocusRef) -> dict[str, Any]:
        """读取 Job 对象详情（去除 managedFields 以减小体积）。"""
        if not ref.namespace:
            return {"error": "job namespace required"}
        try:
            obj = self.batch.read_namespaced_job(name=ref.name, namespace=ref.namespace)
            d = self.api_client.sanitize_for_serialization(obj)
            d.pop("managedFields", None)
            return d
        except Exception as e:
            return {"error": truncate_text(str(e), max_chars=300), "permission_denied": _is_permission_denied(e)}

    def _allowed(self, permissions: dict[str, Any], *, group: str, resource: str, verb: str) -> bool:
        """从权限探测结果中判断是否允许访问某资源/动词。"""
        for c in permissions.get("checks", []):
            if c.get("group") == group and c.get("resource") == resource and c.get("verb") == verb:
                return bool(c.get("allowed"))
        return False

    def _missing_perm_finding(self, what: str, error: Optional[Exception] = None) -> dict[str, Any]:
        """构造“缺少权限”的统一 findings 结构，便于上层汇总与提示管理员授权。"""
        msg = f"缺少权限：{what}"
        extra = None
        if error is not None:
            extra = truncate_text(str(error), max_chars=300)
        fid = stable_id("missing_permission", what, extra or "")
        return {
            "id": fid,
            "severity": "P2",
            "type": "missing_permission",
            "title": "缺少巡检权限",
            "symptom": msg,
            "evidence": [Evidence(kind="permission", ref={"check": what}, message=extra or msg).to_dict()],
            "focus_refs": [],
            "missing": {"resource": what},
        }
