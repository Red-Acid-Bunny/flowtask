#!/bin/bash
# flowtask bash module: list_dir
# Простой модуль для листинга директории (пример использования become)
#
# Input:
#   $1 (optional) — base64-encoded JSON (при become)
#   stdin — JSON payload (без become)
# Output (stdout): {"status": "ok|error", "message": "...", "changed": false, "data": {"files": [...]}}

set -eo pipefail

# Чтение JSON: из base64 аргумента (become) или stdin (обычный режим)
if [ -n "${1:-}" ]; then
  input=$(echo "$1" | base64 -d)
else
  input=$(cat)
fi

read_param() {
  echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('$1','$2'))"
}

read_flag() {
  echo "$input" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('$1','$2')).lower())"
}

path=$(read_param "path" "/")
dry_run=$(read_flag "dry_run" "false")

# Валидация
if [ ! -d "$path" ]; then
  echo "{\"status\":\"error\",\"message\":\"Directory not found: ${path}\"}"
  exit 1
fi

# Dry run
if [ "$dry_run" = "true" ]; then
  echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would list ${path}\",\"changed\":false}"
  exit 0
fi

# Листинг (включая скрытые файлы)
file_list=$(ls -1a "$path" 2>/dev/null | grep -v '^\.\.\?$' || true)

# Формируем JSON-массив файлов и считаем количество
files_json=$(echo "$file_list" | python3 -c "
import sys, json
files = [line.strip() for line in sys.stdin if line.strip()]
print(json.dumps(files))
")
file_count=$(echo "$files_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

# Выводим список файлов на stderr для логирования
if [ -n "$file_list" ]; then
  >&2 echo "[INFO] Files in $path:"
  while IFS= read -r f; do
    [ -n "$f" ] && >&2 echo "  $f"
  done <<< "$file_list"
else
  >&2 echo "[INFO] Files in $path: (empty)"
fi

echo "{\"status\":\"ok\",\"message\":\"Listed ${file_count} files in ${path}\",\"changed\":false,\"data\":{\"path\":\"${path}\",\"count\":${file_count},\"files\":${files_json}}}"
