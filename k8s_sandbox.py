import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _normalize_rbac_profile(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    v = raw.strip().lower().replace("_", "-")
    aliases = {
        "readonly": "readonly",
        "read-only": "readonly",
        "ro": "readonly",
        "cluster-readonly": "cluster-readonly",
        "cluster-reader": "cluster-readonly",
        "cluster-ro": "cluster-readonly",
    }
    return aliases.get(v, v)


def _load_kube_config() -> None:
    kubeconfig = os.getenv("KUBECONFIG")
    try:
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
            config.load_kube_config()
        return
    except Exception:
        config.load_incluster_config()


def _get_apis() -> Tuple[client.CoreV1Api, client.RbacAuthorizationV1Api, client.ApiClient]:
    _load_kube_config()
    api_client = client.ApiClient()
    return client.CoreV1Api(api_client), client.RbacAuthorizationV1Api(api_client), api_client


def _create_or_ignore(func, *, body: Dict[str, Any], namespace: str) -> None:
    try:
        func(namespace=namespace, body=body)
    except ApiException as e:
        if e.status == 409:
            return
        raise


def _create_or_ignore_cluster(func, *, body: Dict[str, Any]) -> None:
    try:
        func(body=body)
    except ApiException as e:
        if e.status == 409:
            return
        raise


def _delete_ignore_not_found(func, *, name: str, namespace: str) -> None:
    try:
        func(
            name=name,
            namespace=namespace,
            grace_period_seconds=0,
            propagation_policy="Background",
        )
    except ApiException as e:
        if e.status == 404:
            return
        raise


def _delete_ignore_not_found_cluster(func, *, name: str) -> None:
    try:
        func(
            name=name,
            grace_period_seconds=0,
            propagation_policy="Background",
        )
    except ApiException as e:
        if e.status == 404:
            return
        raise


def _pod_failure_reason(pod_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    status = pod_dict.get("status") or {}
    container_statuses = status.get("containerStatuses") or []
    for cs in container_statuses:
        state = (cs or {}).get("state") or {}
        waiting = state.get("waiting")
        if not waiting:
            continue
        reason = waiting.get("reason")
        if reason in {"ErrImagePull", "ImagePullBackOff", "CrashLoopBackOff"}:
            return {"reason": reason, "message": waiting.get("message")}
    return None


def _wait_pod_ready(
    core_v1: client.CoreV1Api,
    api_client: client.ApiClient,
    *,
    namespace: str,
    pod_name: str,
    timeout_seconds: int,
) -> None:
    deadline = time.time() + timeout_seconds
    last_snapshot: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        pod_obj = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        pod_dict = api_client.sanitize_for_serialization(pod_obj)
        last_snapshot = pod_dict

        failure = _pod_failure_reason(pod_dict)
        if failure:
            raise RuntimeError(
                json.dumps(
                    {
                        "error": "pod_start_failed",
                        "namespace": namespace,
                        "pod_name": pod_name,
                        **failure,
                    },
                    ensure_ascii=False,
                )
            )

        conditions = (pod_dict.get("status") or {}).get("conditions") or []
        for c in conditions:
            if c.get("type") == "Ready" and c.get("status") == "True":
                return

        time.sleep(2)

    raise RuntimeError(
        json.dumps(
            {
                "error": "wait_ready_timeout",
                "namespace": namespace,
                "pod_name": pod_name,
                "last_status": (last_snapshot or {}).get("status"),
            },
            ensure_ascii=False,
        )
    )


def _labels(sandbox_id: str) -> Dict[str, str]:
    return {"app": "k8s-sandbox", "sandbox-id": sandbox_id}


def _build_readonly_role(namespace: str, name: str, allow_exec: bool, sandbox_id: str) -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = [
        {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["get", "list", "watch"],
        },
        {
            "apiGroups": [""],
            "resources": ["pods/log"],
            "verbs": ["get"],
        },
        {
            "apiGroups": [""],
            "resources": ["services", "endpoints"],
            "verbs": ["get", "list", "watch"],
        },
        {
            "apiGroups": [""],
            "resources": ["events"],
            "verbs": ["get", "list", "watch"],
        },
    ]
    if allow_exec:
        rules.append(
            {
                "apiGroups": [""],
                "resources": ["pods/exec"],
                "verbs": ["create"],
            }
        )
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": name, "namespace": namespace, "labels": _labels(sandbox_id)},
        "rules": rules,
    }


def _build_cluster_readonly_clusterrole(name: str, allow_exec: bool, sandbox_id: str) -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = [
        {
            "apiGroups": [""],
            "resources": [
                "namespaces",
                "nodes",
                "pods",
                "pods/log",
                "services",
                "endpoints",
                "events",
                "configmaps",
                "persistentvolumes",
                "persistentvolumeclaims",
            ],
            "verbs": ["get", "list", "watch"],
        },
        {
            "apiGroups": ["apps"],
            "resources": ["deployments", "replicasets", "statefulsets", "daemonsets"],
            "verbs": ["get", "list", "watch"],
        },
        {
            "apiGroups": ["batch"],
            "resources": ["jobs", "cronjobs"],
            "verbs": ["get", "list", "watch"],
        },
        {
            "apiGroups": ["storage.k8s.io"],
            "resources": ["storageclasses"],
            "verbs": ["get", "list", "watch"],
        },
    ]
    if allow_exec:
        rules.append(
            {
                "apiGroups": [""],
                "resources": ["pods/exec"],
                "verbs": ["create"],
            }
        )
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {"name": name, "labels": _labels(sandbox_id)},
        "rules": rules,
    }


def _build_role_binding(
    namespace: str,
    name: str,
    role_name: str,
    service_account_name: str,
    sandbox_id: str,
) -> Dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": name, "namespace": namespace, "labels": _labels(sandbox_id)},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role_name},
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": service_account_name,
                "namespace": namespace,
            }
        ],
    }


def _build_cluster_role_binding(
    namespace: str,
    name: str,
    cluster_role_name: str,
    service_account_name: str,
    sandbox_id: str,
) -> Dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": name, "labels": _labels(sandbox_id)},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": cluster_role_name},
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": service_account_name,
                "namespace": namespace,
            }
        ],
    }


def _build_service_account(namespace: str, name: str, sandbox_id: str) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": name, "namespace": namespace, "labels": _labels(sandbox_id)},
        "automountServiceAccountToken": True,
    }


def _build_pod(
    namespace: str,
    name: str,
    image: str,
    ttl_seconds: int,
    service_account_name: str,
    sandbox_id: str,
    read_only_root_filesystem: bool,
) -> Dict[str, Any]:
    pod: Dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(sandbox_id),
        },
        "spec": {
            "restartPolicy": "Never",
            "serviceAccountName": service_account_name,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumes": [{"name": "tmp", "emptyDir": {}}],
            "containers": [
                {
                    "name": "sandbox",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "infinity"],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "200m", "memory": "256Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": read_only_root_filesystem,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
        },
    }
    return pod


def render_sandbox_manifests(
    *,
    namespace: Optional[str] = None,
    image: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    rbac_profile: Optional[str] = None,
    allow_exec: Optional[bool] = None,
    read_only_root_filesystem: Optional[bool] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """生成沙箱所需的 K8S manifests（ServiceAccount/Role/RoleBinding/Pod）。

    返回包含 manifest（K8S List 对象）的结构化数据，供 dry-run 审阅或后续 apply。
    """
    ns = namespace or os.getenv("SANDBOX_NAMESPACE") or "default"
    img = image or os.getenv("SANDBOX_IMAGE") or "busybox:1.36"
    ttl = ttl_seconds if ttl_seconds is not None else _env_int("SANDBOX_TTL_SECONDS", 900)
    profile = _normalize_rbac_profile(rbac_profile or os.getenv("SANDBOX_RBAC_PROFILE") or "cluster-readonly")
    exec_enabled = allow_exec if allow_exec is not None else _env_bool("SANDBOX_ALLOW_EXEC", True)
    ro_root = (
        read_only_root_filesystem
        if read_only_root_filesystem is not None
        else _env_bool("SANDBOX_READONLY_ROOTFS", True)
    )
    sid = sandbox_id or uuid.uuid4().hex

    if ttl <= 0:
        raise ValueError("ttl_seconds must be > 0")
    if profile not in {"readonly", "cluster-readonly"}:
        raise ValueError("unsupported rbac_profile")

    sa_name = _env_str("SANDBOX_SERVICEACCOUNT_NAME", "k8s-sandbox-sa")
    pod_prefix = _env_str("SANDBOX_POD_PREFIX", "k8s-sandbox")
    pod_name = _env_str("SANDBOX_POD_NAME", f"{pod_prefix}-{sid[:8]}")

    sa = _build_service_account(ns, sa_name, sid)
    pod = _build_pod(ns, pod_name, img, ttl, sa_name, sid, ro_root)

    items: List[Dict[str, Any]] = [sa]
    role_name: Optional[str] = None
    role_binding_name: Optional[str] = None
    cluster_role_name: Optional[str] = None
    cluster_role_binding_name: Optional[str] = None

    if profile == "readonly":
        role_name = _env_str("SANDBOX_ROLE_NAME", "k8s-sandbox-readonly")
        role_binding_name = _env_str("SANDBOX_ROLEBINDING_NAME", "k8s-sandbox-readonly-binding")
        role = _build_readonly_role(ns, role_name, exec_enabled, sid)
        rb = _build_role_binding(ns, role_binding_name, role_name, sa_name, sid)
        items.extend([role, rb])
    else:
        cluster_role_name = _env_str("SANDBOX_CLUSTERROLE_NAME", "k8s-sandbox-cluster-readonly")
        cluster_role_binding_name = _env_str(
            "SANDBOX_CLUSTERROLEBINDING_NAME",
            "k8s-sandbox-cluster-readonly-binding",
        )
        cr = _build_cluster_readonly_clusterrole(cluster_role_name, exec_enabled, sid)
        crb = _build_cluster_role_binding(ns, cluster_role_binding_name, cluster_role_name, sa_name, sid)
        items.extend([cr, crb])

    items.append(pod)

    manifest = {"apiVersion": "v1", "kind": "List", "items": items}
    return {
        "sandbox_id": sid,
        "namespace": ns,
        "pod_name": pod_name,
        "label_selector": "app=k8s-sandbox",
        "service_account_name": sa_name,
        "role_name": role_name,
        "role_binding_name": role_binding_name,
        "cluster_role_name": cluster_role_name,
        "cluster_role_binding_name": cluster_role_binding_name,
        "rbac_profile": profile,
        "allow_exec": exec_enabled,
        "read_only_root_filesystem": ro_root,
        "manifest": manifest,
    }


def create_sandbox(
    *,
    namespace: Optional[str] = None,
    image: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    rbac_profile: Optional[str] = None,
    allow_exec: Optional[bool] = None,
    read_only_root_filesystem: Optional[bool] = None,
    dry_run: bool = False,
    wait_ready: bool = True,
    apply_rbac: Optional[bool] = None,
) -> Dict[str, Any]:
    """创建 K8S 沙箱 Pod（以及最小 RBAC 资源）。

    - dry_run=True 时不触发集群操作，只返回 manifest_json 方便审阅
    - wait_ready=True 时等待 Pod Ready
    """
    if not dry_run:
        ns = namespace or os.getenv("SANDBOX_NAMESPACE") or "default"
        core_v1, _, api_client = _get_apis()
        label_selector = "app=k8s-sandbox"
        pods = core_v1.list_namespaced_pod(namespace=ns, label_selector=label_selector)
        items = list(getattr(pods, "items", []) or [])

        if items:
            expected_sa = _env_str("SANDBOX_SERVICEACCOUNT_NAME", "k8s-sandbox-sa")
            filtered = [
                p
                for p in items
                if getattr(getattr(p, "spec", None), "service_account_name", None) == expected_sa
            ]
            candidates = filtered or items

            def sort_key(p) -> Tuple[int, float]:
                status = getattr(p, "status", None)
                phase = getattr(status, "phase", None)
                conditions = getattr(status, "conditions", None) or []
                ready = any(
                    getattr(c, "type", None) == "Ready" and getattr(c, "status", None) == "True"
                    for c in conditions
                )
                created = getattr(getattr(p, "metadata", None), "creation_timestamp", None)
                ts = float(getattr(created, "timestamp", lambda: 0.0)()) if created is not None else 0.0
                return (1 if (phase == "Running" and ready) else 0, ts)

            candidates.sort(key=sort_key, reverse=True)
            selected = candidates[0]
            pod_name = getattr(getattr(selected, "metadata", None), "name", None)
            if not pod_name:
                raise RuntimeError(
                    json.dumps(
                        {
                            "error": "pod_name_missing",
                            "namespace": ns,
                            "label_selector": label_selector,
                        },
                        ensure_ascii=False,
                    )
                )

            status = getattr(selected, "status", None)
            phase = getattr(status, "phase", None)
            conditions = getattr(status, "conditions", None) or []
            is_ready = (
                phase == "Running"
                and any(getattr(c, "type", None) == "Ready" and getattr(c, "status", None) == "True" for c in conditions)
            )
            if wait_ready and not is_ready:
                _wait_pod_ready(core_v1, api_client, namespace=ns, pod_name=pod_name, timeout_seconds=90)

            return {
                "sandbox_id": "existing",
                "namespace": ns,
                "pod_name": pod_name,
                "label_selector": label_selector,
                "service_account_name": getattr(getattr(selected, "spec", None), "service_account_name", None),
                "role_name": None,
                "role_binding_name": None,
                "cluster_role_name": None,
                "cluster_role_binding_name": None,
                "rbac_profile": None,
                "allow_exec": None,
                "read_only_root_filesystem": None,
                "manifest": None,
                "reused": True,
            }

    data = render_sandbox_manifests(
        namespace=namespace,
        image=image,
        ttl_seconds=ttl_seconds,
        rbac_profile=rbac_profile,
        allow_exec=allow_exec,
        read_only_root_filesystem=read_only_root_filesystem,
    )

    if dry_run:
        return {**data, "manifest_json": json.dumps(data["manifest"], indent=2, ensure_ascii=False)}

    ns = data["namespace"]
    core_v1, rbac_v1, api_client = _get_apis()
    should_apply_rbac = apply_rbac if apply_rbac is not None else _env_bool("SANDBOX_APPLY_RBAC", False)
    items = (data["manifest"] or {}).get("items") or []
    sa = next(x for x in items if x.get("kind") == "ServiceAccount")
    pod = next(x for x in items if x.get("kind") == "Pod")

    _create_or_ignore(core_v1.create_namespaced_service_account, body=sa, namespace=ns)
    if should_apply_rbac:
        if data.get("rbac_profile") == "readonly":
            role = next(x for x in items if x.get("kind") == "Role")
            rb = next(x for x in items if x.get("kind") == "RoleBinding")
            _create_or_ignore(rbac_v1.create_namespaced_role, body=role, namespace=ns)
            _create_or_ignore(rbac_v1.create_namespaced_role_binding, body=rb, namespace=ns)
        else:
            cr = next(x for x in items if x.get("kind") == "ClusterRole")
            crb = next(x for x in items if x.get("kind") == "ClusterRoleBinding")
            _create_or_ignore_cluster(rbac_v1.create_cluster_role, body=cr)
            _create_or_ignore_cluster(rbac_v1.create_cluster_role_binding, body=crb)
    _create_or_ignore(core_v1.create_namespaced_pod, body=pod, namespace=ns)

    if wait_ready:
        pod_name = data["pod_name"]
        _wait_pod_ready(core_v1, api_client, namespace=ns, pod_name=pod_name, timeout_seconds=90)

    return data


def exec_in_sandbox(
    *,
    namespace: Optional[str] = None,
    pod_name: Optional[str] = None,
    label_selector: Optional[str] = None,
    container: Optional[str] = None,
    command: Optional[List[str]] = None,
    commands: Optional[Any] = None,
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """在沙箱 Pod 内执行命令并返回 stdout/stderr/exitCode。

    command 必须是字符串列表，会通过 Kubernetes exec API 执行，避免 shell 注入。
    """
    ns = namespace or os.getenv("SANDBOX_NAMESPACE") or "default"
    cmd = command if command is not None else commands
    if isinstance(cmd, str):
        try:
            cmd = json.loads(cmd)
        except Exception as e:
            raise ValueError("command must be a non-empty list of strings") from e
    if not isinstance(cmd, list) or not cmd or any(not isinstance(x, str) or x == "" for x in cmd):
        raise ValueError("command must be a non-empty list of strings")
    if pod_name is not None and label_selector:
        raise ValueError("must not provide both pod_name and label_selector")
    if pod_name is None and not label_selector:
        label_selector = "app=k8s-sandbox"
    core_v1, _, _ = _get_apis()
    selected_label_selector: Optional[str] = None
    selected_container: Optional[str] = container

    def choose_container(pod_obj) -> Optional[str]:
        spec = getattr(pod_obj, "spec", None)
        containers = getattr(spec, "containers", None) or []
        names = [getattr(c, "name", None) for c in containers if getattr(c, "name", None)]
        if not names:
            return None
        if "sandbox" in names:
            return "sandbox"
        return names[0]

    if pod_name is None:
        selected_label_selector = label_selector
        pods = core_v1.list_namespaced_pod(namespace=ns, label_selector=label_selector)
        items = list(getattr(pods, "items", []) or [])
        if not items:
            raise RuntimeError(
                json.dumps(
                    {
                        "error": "no_pod_matched",
                        "namespace": ns,
                        "label_selector": label_selector,
                    },
                    ensure_ascii=False,
                )
            )

        def sort_key(p) -> Tuple[int, float]:
            status = getattr(p, "status", None)
            phase = getattr(status, "phase", None)
            conditions = getattr(status, "conditions", None) or []
            ready = any(getattr(c, "type", None) == "Ready" and getattr(c, "status", None) == "True" for c in conditions)
            created = getattr(getattr(p, "metadata", None), "creation_timestamp", None)
            ts = float(getattr(created, "timestamp", lambda: 0.0)()) if created is not None else 0.0
            return (1 if (phase == "Running" and ready) else 0, ts)

        items.sort(key=sort_key, reverse=True)
        selected_pod = items[0]
        if selected_container is None:
            selected_container = choose_container(selected_pod)
        pod_name = getattr(getattr(selected_pod, "metadata", None), "name", None)
        if not pod_name:
            raise RuntimeError(
                json.dumps(
                    {
                        "error": "pod_name_missing",
                        "namespace": ns,
                        "label_selector": label_selector,
                    },
                    ensure_ascii=False,
                )
            )
    elif selected_container is None:
        pod_obj = core_v1.read_namespaced_pod(name=pod_name, namespace=ns)
        selected_container = choose_container(pod_obj)

    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        ns,
        command=cmd,
        **({"container": selected_container} if selected_container else {}),
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
    )
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    exit_code: Optional[int] = None
    start = time.time()
    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            stdout_chunks.append(resp.read_stdout())
        if resp.peek_stderr():
            stderr_chunks.append(resp.read_stderr())
        if time.time() - start > timeout_seconds:
            resp.close()
            raise TimeoutError("exec timeout")
    for c in getattr(resp, "channel", {}).values():
        if isinstance(c, str) and "exit code" in c.lower():
            digits = "".join(ch for ch in c if ch.isdigit())
            if digits:
                exit_code = int(digits)
                break
    stdout = "".join(stdout_chunks).strip()
    stderr = "".join(stderr_chunks).strip()
    lowered = stderr.lower()
    permission_denied = any(x in lowered for x in ("forbidden", "unauthorized", "cannot list", "cannot get"))
    return {
        "namespace": ns,
        "pod_name": pod_name,
        "label_selector": selected_label_selector,
        "container": selected_container,
        "command": cmd,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "permission_denied": permission_denied,
    }


def cleanup_sandbox(
    *,
    namespace: str,
    pod_name: str,
    service_account_name: Optional[str] = None,
    role_name: Optional[str] = None,
    role_binding_name: Optional[str] = None,
    cluster_role_name: Optional[str] = None,
    cluster_role_binding_name: Optional[str] = None,
) -> None:
    core_v1, rbac_v1, _ = _get_apis()
    if pod_name:
        _delete_ignore_not_found(core_v1.delete_namespaced_pod, name=pod_name, namespace=namespace)
    if service_account_name:
        _delete_ignore_not_found(core_v1.delete_namespaced_service_account, name=service_account_name, namespace=namespace)
    if role_name:
        _delete_ignore_not_found(rbac_v1.delete_namespaced_role, name=role_name, namespace=namespace)
    if role_binding_name:
        _delete_ignore_not_found(rbac_v1.delete_namespaced_role_binding, name=role_binding_name, namespace=namespace)
    if cluster_role_binding_name:
        _delete_ignore_not_found_cluster(rbac_v1.delete_cluster_role_binding, name=cluster_role_binding_name)
    if cluster_role_name:
        _delete_ignore_not_found_cluster(rbac_v1.delete_cluster_role, name=cluster_role_name)
