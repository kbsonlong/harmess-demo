from __future__ import annotations

import argparse
import sys

from .inspector import Inspector, InspectorConfig
from .types import FocusRef
from .utils import json_dumps, parse_duration_seconds


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器（run/focus）。"""
    p = argparse.ArgumentParser(prog="sandbox-inspector")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--time-window", default="2h")
    p_run.add_argument("--max-findings", type=int, default=50)
    p_run.add_argument("--max-items-scanned", type=int, default=2000)
    p_run.add_argument("--evidence-max-chars", type=int, default=800)
    p_run.add_argument("--evidence-max-lines", type=int, default=40)
    p_run.add_argument("--log-tail-lines", type=int, default=200)

    p_focus = sub.add_parser("focus")
    p_focus.add_argument("--kind", required=True)
    p_focus.add_argument("--name", required=True)
    p_focus.add_argument("--namespace", default=None)
    p_focus.add_argument("--container", default=None)
    p_focus.add_argument("--no-logs", action="store_true")
    p_focus.add_argument("--no-events", action="store_true")
    p_focus.add_argument("--log-tail-lines", type=int, default=200)
    p_focus.add_argument("--log-max-chars", type=int, default=6000)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：输出 JSON 到 stdout；返回进程退出码。"""
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)

    if args.cmd == "run":
        cfg = InspectorConfig(
            time_window_seconds=parse_duration_seconds(args.time_window, default_seconds=7200),
            max_findings=args.max_findings,
            max_items_scanned=args.max_items_scanned,
            evidence_max_chars=args.evidence_max_chars,
            evidence_max_lines=args.evidence_max_lines,
            log_tail_lines=args.log_tail_lines,
        )
        ins = Inspector(cfg)
        out = ins.run()
        print(json_dumps(out))
        return 0

    if args.cmd == "focus":
        cfg = InspectorConfig(log_tail_lines=args.log_tail_lines, log_max_chars=args.log_max_chars)
        ins = Inspector(cfg)
        ref = FocusRef(kind=args.kind, namespace=args.namespace, name=args.name, container=args.container)
        out = ins.focus(ref=ref, include_logs=not args.no_logs, include_events=not args.no_events)
        print(json_dumps(out))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
