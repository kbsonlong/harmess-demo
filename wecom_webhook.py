import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


WECOM_MARKDOWN_MAX_CHARS = 4096


@dataclass(frozen=True)
class WecomSendResult:
    ok: bool
    status_code: Optional[int]
    errmsg: str


def _truncate_markdown(content: str, limit: int = WECOM_MARKDOWN_MAX_CHARS) -> str:
    if len(content) <= limit:
        return content
    suffix = "\n\n> 内容过长已截断"
    keep = max(0, limit - len(suffix))
    return content[:keep] + suffix


def send_wecom_markdown(webhook_url: str, content: str, timeout_s: float = 10.0) -> WecomSendResult:
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": _truncate_markdown(content)},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None)
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}
            errcode = data.get("errcode")
            errmsg = data.get("errmsg") or raw or ""
            ok = (status == 200) and (errcode in (0, "0", None))
            return WecomSendResult(ok=ok, status_code=status, errmsg=str(errmsg))
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return WecomSendResult(ok=False, status_code=getattr(e, "code", None), errmsg=raw or str(e))
    except Exception as e:
        return WecomSendResult(ok=False, status_code=None, errmsg=str(e))


def wecom_webhook_url_from_env() -> Optional[str]:
    return os.environ.get("WECOM_WEBHOOK_URL") or os.environ.get("WEWORK_WEBHOOK_URL")


def find_report_path(reports_dir: Path, thread_id: Optional[str] = None, max_age_s: float = 600.0) -> Optional[Path]:
    if thread_id:
        candidate = reports_dir / f"inspection_report-{thread_id}.md"
        if candidate.exists():
            return candidate
    now = time.time()
    candidates = sorted(reports_dir.glob("inspection_report-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        try:
            if (now - p.stat().st_mtime) <= max_age_s:
                return p
        except FileNotFoundError:
            continue
    return None


def build_wecom_markdown_from_report(report_path: Path) -> str:
    content = report_path.read_text(encoding="utf-8", errors="replace").strip()
    header = f"# Kubernetes 巡检报告\n\n> 文件：{report_path.name}\n\n"
    return header + content

