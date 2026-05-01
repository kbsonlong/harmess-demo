import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from agent_core.config import get_project_paths
from agent_core.kubeconfig import ensure_kubeconfig_env_default
from kind_demo import kind_down, kind_up
from release_metadata import build_release_metadata, generate_thread_id, write_release_metadata


def _ensure_tool(name: str) -> None:
    if subprocess.run(["bash", "-lc", f"command -v {name} >/dev/null 2>&1"]).returncode != 0:
        raise RuntimeError(f"missing: {name}")


def _run_checked(cmd: list[str]) -> None:
    ensure_kubeconfig_env_default()
    subprocess.run(cmd, check=True, env=os.environ.copy())


def _run_capture(cmd: list[str]) -> dict[str, Any]:
    ensure_kubeconfig_env_default()
    p = subprocess.run(cmd, capture_output=True, text=True, check=False, env=os.environ.copy())
    return {"exit_code": p.returncode, "stdout": (p.stdout or "").strip(), "stderr": (p.stderr or "").strip()}


def _load_kube_config() -> None:
    kubeconfig = ensure_kubeconfig_env_default()
    config.load_kube_config(config_file=kubeconfig)


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json_report(*, reports_dir: str, filename: str, payload: dict[str, Any]) -> Path:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _try_load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _deployment_label_selector(dep: dict[str, Any]) -> str:
    labels = (((dep.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}) if dep else {}
    if not labels:
        return ""
    return ",".join([f"{k}={labels[k]}" for k in sorted(labels.keys())])


def _extract_waiting_reason_hit(pod: dict[str, Any], expected_reasons: set[str]) -> Optional[dict[str, Any]]:
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    for cs in statuses:
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        reason = waiting.get("reason")
        if reason and reason in expected_reasons:
            meta = pod.get("metadata") or {}
            return {
                "namespace": meta.get("namespace"),
                "pod": meta.get("name"),
                "container": cs.get("name"),
                "reason": reason,
                "message": waiting.get("message"),
                "restart_count": cs.get("restartCount"),
            }
    return None


def _try_disable_argocd_autosync(*, namespace: str, application_name: str) -> dict[str, Any]:
    try:
        _load_kube_config()
        co = client.CustomObjectsApi(client.ApiClient())
        co.patch_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
            name=application_name,
            body={"spec": {"syncPolicy": {"automated": None}}},
        )
        return {"ok": True}
    except ApiException as e:
        return {"ok": False, "error": f"ApiException({e.status})", "body": getattr(e, "body", None)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _try_patch_demo_deployment(
    *,
    namespace: str,
    deployment_name: str,
    mode: Literal["imagepull", "crashloop"],
    invalid_image: str,
    crash_image: str,
) -> dict[str, Any]:
    try:
        _load_kube_config()
        api_client = client.ApiClient()
        apps = client.AppsV1Api(api_client)
        dep_obj = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        dep = api_client.sanitize_for_serialization(dep_obj)
        containers = (((dep.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
        if not containers:
            return {"ok": False, "error": "no_containers"}

        c0 = containers[0] or {}
        c0_name = c0.get("name") or "guestbook-ui"
        previous = {"image": c0.get("image"), "command": c0.get("command"), "args": c0.get("args")}

        if mode == "imagepull":
            patch = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": c0_name,
                                    "image": invalid_image,
                                    "imagePullPolicy": "Always",
                                }
                            ]
                        }
                    }
                }
            }
        else:
            patch = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": c0_name,
                                    "image": crash_image,
                                    "command": ["sh", "-c"],
                                    "args": ["echo injected-crashloop; sleep 1; exit 1"],
                                }
                            ]
                        }
                    }
                }
            }

        apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch)
        return {"ok": True, "previous": previous, "patched_container": {"name": c0_name, "mode": mode}}
    except ApiException as e:
        return {"ok": False, "error": f"ApiException({e.status})", "body": getattr(e, "body", None)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _wait_failure(
    *,
    mode: Literal["imagepull", "crashloop"],
    namespace: str,
    deployment_name: str,
    timeout_s: int,
    poll_s: float,
) -> dict[str, Any]:
    expected = {"imagepull": {"ErrImagePull", "ImagePullBackOff"}, "crashloop": {"CrashLoopBackOff"}}[mode]
    deadline = time.time() + timeout_s

    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            _load_kube_config()
            api_client = client.ApiClient()
            apps = client.AppsV1Api(api_client)
            core = client.CoreV1Api(api_client)
            dep_obj = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
            dep = api_client.sanitize_for_serialization(dep_obj)
            selector = _deployment_label_selector(dep)
            if not selector:
                return {"ok": False, "error": "missing_deployment_selector"}

            pods_obj = core.list_namespaced_pod(namespace=namespace, label_selector=selector)
            pods = [api_client.sanitize_for_serialization(p) for p in (pods_obj.items or [])]
            last = {"selector": selector, "pods": [{"name": (p.get("metadata") or {}).get("name")} for p in pods]}

            hits: list[dict[str, Any]] = []
            for p in pods:
                hit = _extract_waiting_reason_hit(p, expected)
                if hit:
                    hits.append(hit)
            if hits:
                return {"ok": True, "expected_reasons": sorted(expected), "hits": hits, "selector": selector}
        except Exception as e:
            last = {"error": str(e)}

        time.sleep(poll_s)
    return {"ok": False, "error": "timeout", "last": last}



def _current_kube_context() -> Optional[str]:
    res = _run_capture(["kubectl", "config", "current-context"])
    if res["exit_code"] != 0:
        return None
    return res["stdout"] or None


def _kubernetes_version() -> Optional[str]:
    try:
        _load_kube_config()
        version = client.VersionApi(client.ApiClient()).get_code()
        return getattr(version, "git_version", None) or getattr(version, "gitVersion", None)
    except Exception:
        return None


def _try_read_deployment_image(namespace: str, name: str) -> Optional[str]:
    try:
        _load_kube_config()
        apps = client.AppsV1Api(client.ApiClient())
        dep = apps.read_namespaced_deployment(name=name, namespace=namespace)
        containers = (dep.spec.template.spec.containers or []) if dep and dep.spec and dep.spec.template else []
        if not containers:
            return None
        return containers[0].image
    except Exception:
        return None


def _try_read_service_nodeports(namespace: str, name: str) -> Optional[dict[str, Any]]:
    try:
        _load_kube_config()
        core = client.CoreV1Api(client.ApiClient())
        svc = core.read_namespaced_service(name=name, namespace=namespace)
        ports = []
        for p in svc.spec.ports or []:
            ports.append(
                {
                    "name": p.name,
                    "port": p.port,
                    "target_port": getattr(p, "target_port", None) or getattr(p, "targetPort", None),
                    "node_port": getattr(p, "node_port", None) or getattr(p, "nodePort", None),
                    "protocol": p.protocol,
                }
            )
        return {"type": svc.spec.type, "ports": ports}
    except Exception:
        return None


def _try_get_argocd_application_status(name: str, namespace: str) -> Optional[dict[str, Any]]:
    try:
        _load_kube_config()
        co = client.CustomObjectsApi(client.ApiClient())
        obj = co.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
            name=name,
        )
        status = (obj or {}).get("status") or {}
        sync = status.get("sync") or {}
        health = status.get("health") or {}
        return {
            "sync": {"status": sync.get("status"), "revision": sync.get("revision")},
            "health": {"status": health.get("status")},
        }
    except ApiException:
        return None
    except Exception:
        return None


def _wait_kubectl_get_ok(*, get_cmd: list[str], timeout_s: int, poll_s: float) -> None:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _run_capture(get_cmd)
        if last["exit_code"] == 0:
            return
        time.sleep(poll_s)
    raise RuntimeError({"error": "timeout_waiting_resource", "cmd": get_cmd, "last": last})


def install_argocd(*, manifests_dir: str) -> None:
    _ensure_tool("kubectl")
    _run_checked(["kubectl", "apply", "-k", manifests_dir])


def wait_argocd_ready(*, namespace: str = "argocd", timeout_s: int = 300) -> None:
    _ensure_tool("kubectl")
    _wait_kubectl_get_ok(get_cmd=["kubectl", "get", "ns", namespace], timeout_s=timeout_s, poll_s=1.0)

    for crd in ["applications.argoproj.io", "appprojects.argoproj.io"]:
        _wait_kubectl_get_ok(get_cmd=["kubectl", "get", "crd", crd], timeout_s=timeout_s, poll_s=1.0)
        _run_checked(["kubectl", "wait", "--for=condition=Established", f"crd/{crd}", f"--timeout={timeout_s}s"])

    _wait_kubectl_get_ok(get_cmd=["kubectl", "-n", namespace, "get", "deploy", "argocd-server"], timeout_s=timeout_s, poll_s=1.0)
    _run_checked(
        [
            "kubectl",
            "-n",
            namespace,
            "wait",
            "--for=condition=Available",
            "deploy",
            "--all",
            f"--timeout={timeout_s}s",
        ]
    )


def apply_demo_app(*, application_manifest: str) -> None:
    _ensure_tool("kubectl")
    _run_checked(["kubectl", "apply", "-f", application_manifest])


def collect_and_write_metadata(
    *,
    thread_id: str,
    kind_cluster_name: Optional[str],
    argocd_namespace: str,
    demo_app_namespace: str,
    manifests: list[str],
) -> Path:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    paths = get_project_paths(project_dir)
    kube_context = _current_kube_context()
    k8s_version = _kubernetes_version()

    argocd = {
        "namespace": argocd_namespace,
        "server_deployment_image": _try_read_deployment_image(argocd_namespace, "argocd-server"),
        "server_service": _try_read_service_nodeports(argocd_namespace, "argocd-server"),
    }

    demo_app = {
        "namespace": demo_app_namespace,
        "application": _try_get_argocd_application_status("demo-app", argocd_namespace),
        "workload_deployment_image": _try_read_deployment_image(demo_app_namespace, "guestbook-ui"),
    }

    payload = build_release_metadata(
        thread_id=thread_id,
        project_dir=project_dir,
        kind_cluster_name=kind_cluster_name,
        kube_context=kube_context,
        kubernetes_version=k8s_version,
        argocd=argocd,
        demo_app=demo_app,
        manifests=manifests,
    )
    return write_release_metadata(reports_dir=paths.reports_dir, thread_id=thread_id, payload=payload)


def main() -> None:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    paths = get_project_paths(project_dir)
    argocd_kustomize_dir = os.path.join(project_dir, "manifests", "gitops", "argocd")
    demo_app_manifest = os.path.join(project_dir, "manifests", "gitops", "argocd", "apps", "demo-app.yaml")

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("up")
    p_up.add_argument("--cluster-name", default="demo04")
    p_up.add_argument("--skip-kind", action="store_true")
    p_up.add_argument("--thread-id", default=None)

    p_down = sub.add_parser("down")
    p_down.add_argument("--cluster-name", default="demo04")

    p_meta = sub.add_parser("metadata")
    p_meta.add_argument("--cluster-name", default=None)
    p_meta.add_argument("--thread-id", default=None)

    p_inject = sub.add_parser("inject-failure")
    p_inject.add_argument("--mode", choices=["imagepull", "crashloop"], default="imagepull")
    p_inject.add_argument("--thread-id", default=None)
    p_inject.add_argument("--argocd-namespace", default="argocd")
    p_inject.add_argument("--application", default="demo-app")
    p_inject.add_argument("--namespace", default="demo-app")
    p_inject.add_argument("--deployment", default="guestbook-ui")
    p_inject.add_argument("--invalid-image", default="this-image-should-not-exist.invalid:0")
    p_inject.add_argument("--crash-image", default="busybox:1.36")
    p_inject.add_argument("--keep-autosync", action="store_true")

    p_wait = sub.add_parser("wait-fail")
    p_wait.add_argument("--mode", choices=["imagepull", "crashloop"], default="imagepull")
    p_wait.add_argument("--thread-id", default=None)
    p_wait.add_argument("--namespace", default="demo-app")
    p_wait.add_argument("--deployment", default="guestbook-ui")
    p_wait.add_argument("--timeout-s", type=int, default=180)
    p_wait.add_argument("--poll-s", type=float, default=2.0)

    args = parser.parse_args()

    if args.cmd == "up":
        _ensure_tool("kind")
        if not args.skip_kind:
            kind_up(args.cluster_name)

        install_argocd(manifests_dir=argocd_kustomize_dir)
        wait_argocd_ready(namespace="argocd", timeout_s=300)
        apply_demo_app(application_manifest=demo_app_manifest)
        time.sleep(2)

        thread_id = args.thread_id or generate_thread_id("gitops")
        out = collect_and_write_metadata(
            thread_id=thread_id,
            kind_cluster_name=args.cluster_name,
            argocd_namespace="argocd",
            demo_app_namespace="demo-app",
            manifests=[argocd_kustomize_dir, demo_app_manifest],
        )
        print(str(out))
        return

    if args.cmd == "down":
        _ensure_tool("kind")
        kind_down(args.cluster_name)
        return

    if args.cmd == "metadata":
        thread_id = args.thread_id or generate_thread_id("gitops")
        out = collect_and_write_metadata(
            thread_id=thread_id,
            kind_cluster_name=args.cluster_name,
            argocd_namespace="argocd",
            demo_app_namespace="demo-app",
            manifests=[argocd_kustomize_dir, demo_app_manifest],
        )
        print(str(out))
        return

    if args.cmd == "inject-failure":
        thread_id = args.thread_id or generate_thread_id("release")
        now = time.time()
        window_start = now - 60

        disable_autosync = not bool(args.keep_autosync)
        autosync_res: Optional[dict[str, Any]] = None
        if disable_autosync:
            autosync_res = _try_disable_argocd_autosync(namespace=args.argocd_namespace, application_name=args.application)

        patch_res = _try_patch_demo_deployment(
            namespace=args.namespace,
            deployment_name=args.deployment,
            mode=args.mode,
            invalid_image=args.invalid_image,
            crash_image=args.crash_image,
        )

        payload = {
            "schema_version": "1",
            "release_id": thread_id,
            "thread_id": thread_id,
            "mode": args.mode,
            "injected_at": _iso_utc(now),
            "time_window": {
                "start": _iso_utc(window_start),
                "end": None,
                "start_epoch": window_start,
                "end_epoch": None,
            },
            "objects": [
                {"kind": "Application", "namespace": args.argocd_namespace, "name": args.application},
                {"kind": "Deployment", "namespace": args.namespace, "name": args.deployment},
            ],
            "actions": {
                "disable_autosync": autosync_res,
                "patch_deployment": patch_res,
            },
        }

        out = _write_json_report(reports_dir=paths.reports_dir, filename=f"release_failure-{thread_id}.json", payload=payload)
        payload["report_path"] = str(out)
        print(json.dumps(payload, ensure_ascii=False))
        return

    if args.cmd == "wait-fail":
        thread_id = args.thread_id or generate_thread_id("release")
        existing = _try_load_json(Path(paths.reports_dir) / f"release_failure-{thread_id}.json") or {}
        start_epoch = (
            ((existing.get("time_window") or {}).get("start_epoch")) if isinstance(existing.get("time_window"), dict) else None
        )
        if not isinstance(start_epoch, (int, float)):
            start_epoch = time.time() - 60

        wait_res = _wait_failure(
            mode=args.mode,
            namespace=args.namespace,
            deployment_name=args.deployment,
            timeout_s=int(args.timeout_s),
            poll_s=float(args.poll_s),
        )
        observed_at = time.time()
        window_end = observed_at + 60

        payload = dict(existing)
        payload.update(
            {
                "schema_version": "1",
                "release_id": thread_id,
                "thread_id": thread_id,
                "mode": args.mode,
                "observed_at": _iso_utc(observed_at),
                "time_window": {
                    "start": _iso_utc(float(start_epoch)),
                    "end": _iso_utc(window_end),
                    "start_epoch": float(start_epoch),
                    "end_epoch": float(window_end),
                },
                "wait_result": wait_res,
            }
        )

        out = _write_json_report(reports_dir=paths.reports_dir, filename=f"release_failure-{thread_id}.json", payload=payload)
        payload["report_path"] = str(out)
        print(json.dumps(payload, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
