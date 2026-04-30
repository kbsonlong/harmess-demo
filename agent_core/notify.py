from pathlib import Path
from typing import Optional


def notify_wecom_if_configured(reports_dir: Path, thread_id: str, max_age_s: float = 1800.0) -> None:
    try:
        from wecom_webhook import (
            build_wecom_markdown_from_report,
            find_report_path,
            send_wecom_markdown,
            wecom_webhook_url_from_env,
        )

        webhook_url = wecom_webhook_url_from_env()
        if not webhook_url:
            return
        report_path = find_report_path(reports_dir, thread_id=thread_id, max_age_s=max_age_s)
        if not report_path:
            return
        content = build_wecom_markdown_from_report(report_path)
        send_wecom_markdown(webhook_url, content)
    except Exception:
        return

