import json
import time
from pathlib import Path
from typing import Any, Optional


def _try_load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_latest_release_failure(
    *,
    reports_dir: str,
    thread_id: Optional[str] = None,
    explicit_path: Optional[str] = None,
    max_age_s: float = 24 * 3600,
) -> Optional[dict[str, Any]]:
    reports = Path(reports_dir)
    if explicit_path:
        p = Path(explicit_path)
        if p.exists() and p.is_file():
            return _try_load_json(p)
        return None
    if thread_id:
        p = reports / f"release_failure-{thread_id}.json"
        if p.exists():
            return _try_load_json(p)
        return None
    if not reports.exists():
        return None
    now = time.time()
    candidates = sorted(reports.glob("release_failure-*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    for p in candidates:
        try:
            if (now - p.stat().st_mtime) > max_age_s:
                continue
        except FileNotFoundError:
            continue
        data = _try_load_json(p)
        if data:
            data["_report_path"] = str(p)
            return data
    return None


def summarize_release_failure_context(data: dict[str, Any]) -> dict[str, Any]:
    time_window = data.get("time_window") if isinstance(data.get("time_window"), dict) else {}
    targets = data.get("targets") if isinstance(data.get("targets"), list) else []
    return {
        "schema_version": data.get("schema_version"),
        "release_id": data.get("release_id") or data.get("thread_id"),
        "mode": data.get("mode"),
        "observed_at": data.get("observed_at"),
        "time_window": {
            "start": time_window.get("start"),
            "end": time_window.get("end"),
            "start_epoch": time_window.get("start_epoch"),
            "end_epoch": time_window.get("end_epoch"),
        },
        "targets": [
            {k: t.get(k) for k in ("kind", "namespace", "name") if isinstance(t, dict) and k in t}
            for t in targets
            if isinstance(t, dict)
        ],
        "report_path": data.get("_report_path") or data.get("report_path"),
    }

