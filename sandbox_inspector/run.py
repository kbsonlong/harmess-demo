#!/usr/bin/env python3
"""
沙箱内执行巡检命令
"""
import subprocess
import sys

def run_sandbox_inspector():
    """在沙箱内执行巡检命令"""
    cmd = [
        sys.executable,
        "-m",
        "sandbox_inspector.cli",
        "run",
        "--max-findings",
        "50"
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    
    return result

if __name__ == "__main__":
    run_sandbox_inspector()
