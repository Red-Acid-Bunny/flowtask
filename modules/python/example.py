#!/usr/bin/env python3
"""
FlowTask Python Script Module Template

Этот скрипт демонстрирует JSON-контракт для Python-скриптов в FlowTask.

Input (stdin):
    {
        "action": "module_name",
        "params": {...},
        "context": {
            "vars": {...},
            "secrets": {...},
            "playbook_dir": "/path"
        },
        "dry_run": false,
        "verbose": false
    }

Output (stdout):
    {
        "status": "ok|failed|skipped",
        "changed": true|false,
        "message": "...",
        "data": {...}
    }

Usage:
    python3 /path/to/modules/python/example.py < input.json

Для использования в плейбуке:
    - name: "Example task"
      module: example
      params:
        message: "Hello from Python!"
"""

import json
import sys
from pathlib import Path


def main():
    try:
        # Чтение JSON из stdin
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({
            "status": "failed",
            "changed": False,
            "message": f"Invalid JSON input: {e}",
        }))
        sys.exit(1)

    action = input_data.get("action", "")
    params = input_data.get("params", {})
    context = input_data.get("context", {})
    dry_run = input_data.get("dry_run", False)
    verbose = input_data.get("verbose", False)

    if verbose:
        print(f"[DEBUG] Action: {action}", file=sys.stderr)
        print(f"[DEBUG] Params: {params}", file=sys.stderr)

    # Обработка параметров
    message = params.get("message", "Hello from Python script!")
    count = params.get("count", 1)
    create_file = params.get("create_file", False)
    file_path = params.get("file_path", "/tmp/flowtask_example.txt")

    if dry_run:
        print(json.dumps({
            "status": "ok",
            "changed": False,
            "message": f"[DRY-RUN] Would output '{message}' {count} times",
            "data": {
                "action": action,
                "dry_run": True,
            }
        }))
        sys.exit(0)

    # Выполнение логики
    changed = False
    result_data = {
        "action": action,
        "message_count": count,
    }

    try:
        if create_file:
            output_path = Path(file_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(message * count)
            changed = True
            result_data["file_path"] = str(output_path)
            result_data["file_size"] = output_path.stat().st_size

        print(json.dumps({
            "status": "ok",
            "changed": changed,
            "message": f"Python script executed: {message[:50]}..." if len(message) > 50 else f"Python script executed: {message}",
            "data": result_data,
        }))

    except PermissionError as e:
        print(json.dumps({
            "status": "failed",
            "changed": False,
            "message": f"Permission denied: {e}",
            "data": {"error_type": "PermissionError"},
        }))
        sys.exit(1)

    except Exception as e:
        print(json.dumps({
            "status": "failed",
            "changed": False,
            "message": f"Error: {e}",
            "data": {"error_type": type(e).__name__},
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
