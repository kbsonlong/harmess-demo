import json
import os
import time
from typing import Any, Optional

import urllib.error
import urllib.parse
import urllib.request


def _urlopen(req: urllib.request.Request, *, timeout: int, proxy_url: Optional[str]):
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def victorialogs_query(
    *,
    query: Optional[str] = None,
    queries: Optional[list[dict[str, Any]]] = None,
    base_url: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 50,
    timeout_seconds: int = 20,
    proxy_url: Optional[str] = None,
) -> dict[str, Any]:
    """直连或通过代理查询 VictoriaLogs（支持单条 query 或批量 queries），返回结构化结果。"""
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

    base = (
        (base_url or os.environ.get("VICTORIALOGS_BASE_URL") or "http://victorialogs.observability.svc:9428")
    ).rstrip("/")
    endpoint = f"{base}/select/logsql/query"

    started_at = time.time()
    timeout_int = max(1, min(int(timeout_seconds), 120))
    proxy = proxy_url or os.environ.get("VICTORIALOGS_PROXY_URL")

    out: dict[str, Any] = {"endpoint": endpoint, "generated_at": time.time(), "results": []}
    for q in normalized:
        data: dict[str, Any] = {"query": q["query"], "limit": q.get("limit", 50)}
        if q.get("start"):
            data["start"] = q["start"]
        if q.get("end"):
            data["end"] = q["end"]

        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        q_started = time.time()
        items: list[Any] = []
        errors: list[dict[str, Any]] = []
        status: Optional[int] = None

        try:
            with _urlopen(req, timeout=timeout_int, proxy_url=proxy) as resp:
                status = getattr(resp, "status", None)
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    if len(items) >= int(q.get("limit", 50)):
                        break
                    s = line.decode("utf-8", errors="replace").strip()
                    if not s:
                        continue
                    try:
                        items.append(json.loads(s))
                    except Exception as e:
                        errors.append({"line": s[:2000], "error": str(e)[:300]})
                        if len(errors) >= 3:
                            break
        except urllib.error.HTTPError as e:
            status = getattr(e, "code", None)
            body_preview = ""
            try:
                body_preview = (e.read(2000) or b"").decode("utf-8", errors="replace")
            except Exception:
                body_preview = ""
            errors.append({"error": str(e)[:500], "body": body_preview})
        except Exception as e:
            errors.append({"error": str(e)[:500]})

        out["results"].append(
            {
                "id": q.get("id"),
                "query": q.get("query"),
                "start": q.get("start"),
                "end": q.get("end"),
                "limit": q.get("limit"),
                "http_status": status,
                "duration_s": round(time.time() - q_started, 6),
                "items": items,
                "errors": errors,
            }
        )

    return {
        "tool": "victorialogs_query",
        "input": {
            "base_url": base,
            "endpoint": endpoint,
            "queries": normalized,
            "timeout_seconds": timeout_int,
            "proxy_url": proxy,
        },
        "transport": {
            "mode": "direct",
            "duration_ms": int((time.time() - started_at) * 1000),
        },
        "result": out,
    }
