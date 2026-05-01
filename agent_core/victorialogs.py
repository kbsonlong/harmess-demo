import json
import time
from typing import Any, Optional

from k8s_sandbox import exec_in_sandbox


def victorialogs_query(
    *,
    query: Optional[str] = None,
    queries: Optional[list[dict[str, Any]]] = None,
    base_url: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 50,
    timeout_seconds: int = 20,
    namespace: Optional[str] = None,
    pod_name: Optional[str] = None,
    label_selector: Optional[str] = None,
    container: Optional[str] = None,
    sandbox_timeout_seconds: int = 30,
) -> dict[str, Any]:
    if queries is None:
        if not isinstance(query, str) or not query.strip():
            return {"error": "invalid_query", "message": "query must be non-empty string"}
        queries = [{"query": query, "start": start, "end": end, "limit": limit}]
    if not isinstance(queries, list) or not queries:
        return {"error": "invalid_queries", "message": "queries must be a non-empty list"}

    normalized: list[dict[str, Any]] = []
    for i, q in enumerate(queries):
        if not isinstance(q, dict):
            return {"error": "invalid_queries", "message": f"queries[{i}] must be an object"}
        qq = q.get("query")
        if not isinstance(qq, str) or not qq.strip():
            return {"error": "invalid_queries", "message": f"queries[{i}].query must be non-empty string"}
        q_limit = q.get("limit", limit)
        try:
            q_limit_int = int(q_limit)
        except Exception:
            return {"error": "invalid_queries", "message": f"queries[{i}].limit must be int"}
        q_limit_int = max(1, min(q_limit_int, 200))
        normalized.append(
            {
                "id": q.get("id") if isinstance(q.get("id"), str) and q.get("id") else f"q{i+1}",
                "query": qq,
                "start": q.get("start", start),
                "end": q.get("end", end),
                "limit": q_limit_int,
            }
        )

    base = (base_url or "http://victorialogs.observability.svc:9428").rstrip("/")
    endpoint = f"{base}/select/logsql/query"

    payload = {
        "endpoint": endpoint,
        "timeout_seconds": max(1, min(int(timeout_seconds), 120)),
        "queries": normalized,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    code = (
        "import json,sys,urllib.request,urllib.parse,time\n"
        f"payload=json.loads({payload_json!r})\n"
        "endpoint=payload['endpoint']\n"
        "timeout=payload.get('timeout_seconds',20)\n"
        "queries=payload.get('queries') or []\n"
        "out={'endpoint': endpoint, 'generated_at': time.time(), 'results': []}\n"
        "for q in queries:\n"
        "    data={'query': q['query'], 'limit': q.get('limit',50)}\n"
        "    if q.get('start'):\n"
        "        data['start']=q['start']\n"
        "    if q.get('end'):\n"
        "        data['end']=q['end']\n"
        "    body=urllib.parse.urlencode(data).encode('utf-8')\n"
        "    req=urllib.request.Request(endpoint, data=body, method='POST')\n"
        "    req.add_header('Content-Type','application/x-www-form-urlencoded')\n"
        "    started=time.time()\n"
        "    items=[]\n"
        "    errors=[]\n"
        "    status=None\n"
        "    try:\n"
        "        with urllib.request.urlopen(req, timeout=timeout) as resp:\n"
        "            status=getattr(resp,'status',None)\n"
        "            while True:\n"
        "                line=resp.readline()\n"
        "                if not line:\n"
        "                    break\n"
        "                if len(items) >= int(q.get('limit',50)):\n"
        "                    break\n"
        "                s=line.decode('utf-8', errors='replace').strip()\n"
        "                if not s:\n"
        "                    continue\n"
        "                try:\n"
        "                    items.append(json.loads(s))\n"
        "                except Exception as e:\n"
        "                    errors.append({'line': s[:2000], 'error': str(e)[:300]})\n"
        "                    if len(errors) >= 3:\n"
        "                        break\n"
        "    except Exception as e:\n"
        "        errors.append({'error': str(e)[:500]})\n"
        "    out['results'].append({'id': q.get('id'), 'query': q.get('query'), 'start': q.get('start'), 'end': q.get('end'), 'limit': q.get('limit'), 'http_status': status, 'duration_s': round(time.time()-started, 6), 'items': items, 'errors': errors})\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )

    started_at = time.time()
    exec_res = exec_in_sandbox(
        namespace=namespace,
        pod_name=pod_name,
        label_selector=label_selector,
        container=container,
        command=["python", "-c", code],
        timeout_seconds=int(sandbox_timeout_seconds),
    )
    stdout = (exec_res.get("stdout") or "").strip()
    parsed: Optional[dict[str, Any]] = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except Exception:
            parsed = None

    return {
        "tool": "victorialogs_query",
        "input": {
            "base_url": base,
            "endpoint": endpoint,
            "queries": normalized,
            "timeout_seconds": timeout_seconds,
        },
        "sandbox": {
            "namespace": exec_res.get("namespace"),
            "pod_name": exec_res.get("pod_name"),
            "container": exec_res.get("container"),
            "exit_code": exec_res.get("exit_code"),
            "stderr": (exec_res.get("stderr") or "").strip(),
            "duration_ms": int((time.time() - started_at) * 1000),
        },
        "result": parsed if parsed is not None else {"raw_stdout": stdout[:8000], "parse_error": True},
    }
