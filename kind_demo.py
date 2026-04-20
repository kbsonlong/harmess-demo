import argparse
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from k8s_sandbox import create_sandbox


def _ensure_kind() -> None:
    if shutil.which("kind") is None:
        raise RuntimeError("kind not found in PATH")


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _load_kube_config() -> None:
    kubeconfig = os.getenv("KUBECONFIG")
    if kubeconfig:
        config.load_kube_config(config_file=kubeconfig)
    else:
        config.load_kube_config()


def kind_up(name: str) -> None:
    _ensure_kind()
    _run(["kind", "create", "cluster", "--name", name, "--wait", "60s", "--config", "kind-config.yaml"])


def kind_down(name: str) -> None:
    _ensure_kind()
    _run(["kind", "delete", "cluster", "--name", name])


def _create_namespace_if_missing(core: client.CoreV1Api, name: str) -> None:
    try:
        core.create_namespace(body={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}})
    except ApiException as e:
        if e.status == 409:
            return
        raise


def create_imagepullbackoff_pod(
    *,
    namespace: str,
    pod_name: str,
    image: str,
) -> Dict[str, Any]:
    _load_kube_config()
    api_client = client.ApiClient()
    core = client.CoreV1Api(api_client)

    _create_namespace_if_missing(core, namespace)
    try:
        core.create_namespaced_pod(
            namespace=namespace,
            body={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": pod_name, "namespace": namespace, "labels": {"app": "bad-pod"}},
                "spec": {
                    "restartPolicy": "Always",
                    "containers": [{"name": "bad", "image": image, "imagePullPolicy": "Always"}],
                },
            },
        )
    except ApiException as e:
        if e.status != 409:
            raise

    deadline = time.time() + 60
    last: Optional[dict] = None
    while time.time() < deadline:
        pod_obj = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        pod = api_client.sanitize_for_serialization(pod_obj)
        last = pod
        statuses = (pod.get("status") or {}).get("containerStatuses") or []
        for cs in statuses:
            waiting = ((cs.get("state") or {}).get("waiting") or {})
            reason = waiting.get("reason")
            if reason in {"ErrImagePull", "ImagePullBackOff"}:
                return {"namespace": namespace, "pod_name": pod_name, "reason": reason, "message": waiting.get("message")}
        time.sleep(2)
    return {"namespace": namespace, "pod_name": pod_name, "reason": "timeout", "last_status": (last or {}).get("status")}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("up")
    p_up.add_argument("--name", default="demo04")

    p_down = sub.add_parser("down")
    p_down.add_argument("--name", default="demo04")

    p_bad = sub.add_parser("bad-pod")
    p_bad.add_argument("--namespace", default="sandbox-demo")
    p_bad.add_argument("--pod-name", default="bad-imagepull")
    p_bad.add_argument("--image", default="this-image-should-not-exist.invalid:0")

    p_sandbox = sub.add_parser("sandbox")
    p_sandbox.add_argument("--namespace", default=None)
    p_sandbox.add_argument("--image", default=None, help="覆盖 SANDBOX_IMAGE")

    args = parser.parse_args()

    if args.cmd == "up":
        kind_up(args.name)
        return
    if args.cmd == "down":
        kind_down(args.name)
        return
    if args.cmd == "bad-pod":
        res = create_imagepullbackoff_pod(namespace=args.namespace, pod_name=args.pod_name, image=args.image)
        print(res)
        return
    if args.cmd == "sandbox":
        res = create_sandbox(namespace=args.namespace, image=args.image, dry_run=False, wait_ready=True, apply_rbac=True)
        print(res)
        return


if __name__ == "__main__":
    main()
