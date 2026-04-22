import json
import os
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.stream import stream


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


def _get_core_v1() -> client.CoreV1Api:
    _load_kube_config()
    return client.CoreV1Api(client.ApiClient())


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
    注意：
    - 请确保在执行箱 Pod 内执行命令。
    - 严禁在执行箱 Pod 内执行 -o json 命令。
    - 请务必使用 --field-selector 或 grep 过滤异常数据。
    - 只有当数据量缩减到最小，你才能保持清醒并完成后续任务。

    command 必须是字符串列表，会通过 Kubernetes exec API 执行，避免 shell 注入。
    """
    ns = namespace or os.getenv("SANDBOX_NAMESPACE") or "default"
    cmd = command if command is not None else commands
    if isinstance(cmd, str):
        try:
            cmd = json.loads(cmd)
        except Exception:
            return {
                "namespace": ns,
                "pod_name": pod_name,
                "label_selector": label_selector,
                "container": container,
                "command": cmd,
                "stdout": "",
                "stderr": "command must be a non-empty list of strings",
                "exit_code": None,
                "permission_denied": False,
                "error": "invalid_command",
            }
    if not isinstance(cmd, list) or not cmd or any(not isinstance(x, str) or x == "" for x in cmd):
        return {
            "namespace": ns,
            "pod_name": pod_name,
            "label_selector": label_selector,
            "container": container,
            "command": cmd,
            "stdout": "",
            "stderr": "command must be a non-empty list of strings",
            "exit_code": None,
            "permission_denied": False,
            "error": "invalid_command",
        }
    if pod_name is not None and label_selector:
        return {
            "namespace": ns,
            "pod_name": pod_name,
            "label_selector": label_selector,
            "container": container,
            "command": cmd,
            "stdout": "",
            "stderr": "must not provide both pod_name and label_selector",
            "exit_code": None,
            "permission_denied": False,
            "error": "invalid_arguments",
        }
    if pod_name is None and not label_selector:
        label_selector = "app=k8s-sandbox"
    core_v1 = _get_core_v1()
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
            return {
                "namespace": ns,
                "pod_name": pod_name,
                "label_selector": selected_label_selector,
                "container": selected_container,
                "command": cmd,
                "stdout": "",
                "stderr": json.dumps(
                    {
                        "error": "no_pod_matched",
                        "namespace": ns,
                        "label_selector": label_selector,
                    },
                    ensure_ascii=False,
                ),
                "exit_code": None,
                "permission_denied": False,
                "error": "no_pod_matched",
            }

        def sort_key(p) -> tuple[int, float]:
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
            return {
                "namespace": ns,
                "pod_name": pod_name,
                "label_selector": selected_label_selector,
                "container": selected_container,
                "command": cmd,
                "stdout": "",
                "stderr": json.dumps(
                    {
                        "error": "pod_name_missing",
                        "namespace": ns,
                        "label_selector": label_selector,
                    },
                    ensure_ascii=False,
                ),
                "exit_code": None,
                "permission_denied": False,
                "error": "pod_name_missing",
            }
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
            stdout = "".join(stdout_chunks).strip()
            stderr = "".join(stderr_chunks).strip()
            return {
                "namespace": ns,
                "pod_name": pod_name,
                "label_selector": selected_label_selector,
                "container": selected_container,
                "command": cmd,
                "stdout": stdout,
                "stderr": stderr or "exec timeout",
                "exit_code": None,
                "permission_denied": False,
                "error": "exec_timeout",
            }
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
