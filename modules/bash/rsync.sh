#!/bin/bash
# flowtask bash module: rsync
# Синхронизирует папки через rsync
#
# Input (stdin): {"params": {...}, "dry_run": bool}
#   Параметры:
#     src         — исходная директория (обязательный)
#     dest        — локальная директория назначения (обязательный)
#     folders     — список папок для выгрузки (JSON-массив строк)
#     excludes    — список исключений для rsync (JSON-массив строк)
# Output (stdout): {"status": "ok|error", "message": "...", "changed": bool, "data": {...}}
# Logs: stderr → перехватываются логгером FlowTask

set -eo pipefail

# =============================================
# Чтение параметров из JSON (stdin)
# =============================================
input=$(cat)

read_param() {
  echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('$1','$2'))"
}

read_flag() {
  echo "$input" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('$1','$2')).lower())"
}

src=$(read_param "src" "")
dest=$(read_param "dest" "")
dry_run=$(read_flag "dry_run" "false")

# Резолвим в абсолютные пути (относительные ломаются под sudo)
src=$(realpath -m "$src" 2>/dev/null || echo "$src")
dest=$(realpath -m "$dest" 2>/dev/null || echo "$dest")

# folders и excludes — JSON-массивы
folders_json=$(echo "$input" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['params'].get('folders',[])))")
excludes_json=$(echo "$input" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['params'].get('excludes',[])))")

# =============================================
# Проверка зависимостей
# =============================================
if ! command -v rsync &>/dev/null; then
  echo '{"status":"error","message":"rsync is not installed or not in PATH"}'
  exit 1
fi

# =============================================
# Валидация
# =============================================
if [ -z "$src" ]; then
  echo '{"status":"error","message":"src (source directory) is required"}'
  exit 1
fi

if [ ! -d "$src" ]; then
  echo "{\"status\":\"error\",\"message\":\"src directory not found: ${src}\"}"
  exit 1
fi

if [ -z "$dest" ]; then
  echo '{"status":"error","message":"dest (destination directory) is required"}'
  exit 1
fi

folder_count=$(echo "$folders_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$folder_count" -eq 0 ]; then
  echo '{"status":"error","message":"folders list is empty — nothing to sync"}'
  exit 1
fi

# =============================================
# Dry run
# =============================================
if [ "$dry_run" = "true" ]; then
  folder_list=$(echo "$folders_json" | python3 -c "import sys,json; print(', '.join(json.load(sys.stdin)))")
  echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would sync ${folder_count} folders from ${src} → ${dest}\",\"changed\":false,\"data\":{\"folders\":${folders_json},\"dest\":\"${dest}\"}}"
  exit 0
fi

>&2 echo "[INFO] Sync from $src → $dest ($folder_count folders)"

# =============================================
# Rsync папок
# =============================================
src_base="${src}"
synced=0
failed=0
sync_details=""
exclude_file=""
rsync_out=""

# Cleanup temp files on exit
cleanup() {
  [ -n "$exclude_file" ] && [ -f "$exclude_file" ] && rm -f "$exclude_file"
  [ -n "$rsync_out" ] && [ -f "$rsync_out" ] && rm -f "$rsync_out"
}
trap cleanup EXIT

mkdir -p "$dest"

# Exclude-file вместо eval
exclude_count=$(echo "$excludes_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$exclude_count" -gt 0 ]; then
  exclude_file=$(mktemp /tmp/flowtask_excl_XXXXXX)
  echo "$excludes_json" | python3 -c "import sys,json; [print(x) for x in json.load(sys.stdin)]" >"$exclude_file"
fi

rsync_out=$(mktemp /tmp/flowtask_rsync_XXXXXX)

>&2 echo "[INFO] Source base: $src_base"
>&2 echo "[INFO] Dest: $dest"

while IFS= read -r folder; do
  [ -z "$folder" ] && continue
  src_path="${src_base}/${folder}"

  >&2 echo "[INFO] Syncing folder: $folder"

  if [ ! -d "$src_path" ]; then
    >&2 echo "[WARN] Source not found: $src_path — skipping"
    sync_details="${sync_details}${folder}:not_found "
    failed=$((failed + 1))
    continue
  fi

  # Rsync (без eval, без pipe — надёжная обработка exit code)
  rsync_exit=0
  if [ -n "$exclude_file" ]; then
    rsync -av --delete --exclude-from="$exclude_file" "$src_path" "$dest/" >"$rsync_out" 2>&1 || rsync_exit=$?
  else
    rsync -av --delete "$src_path" "$dest/" >"$rsync_out" 2>&1 || rsync_exit=$?
  fi

  # Логируем вывод rsync
  while IFS= read -r line; do
    >&2 echo "[rsync] $line"
  done <"$rsync_out"

  if [ "$rsync_exit" -eq 0 ]; then
    sync_details="${sync_details}${folder}:ok "
    synced=$((synced + 1))
  else
    sync_details="${sync_details}${folder}:error(${rsync_exit}) "
    failed=$((failed + 1))
  fi
done < <(echo "$folders_json" | python3 -c "import sys,json; [print(x) for x in json.load(sys.stdin)]")

# =============================================
# Результат
# =============================================
if [ "$synced" -gt 0 ]; then
  echo "{\"status\":\"ok\",\"message\":\"Synced ${synced}/${folder_count} folders to ${dest}\",\"changed\":true,\"data\":{\"synced\":${synced},\"failed\":${failed},\"total\":${folder_count},\"dest\":\"${dest}\",\"details\":\"${sync_details}\"}}"
else
  echo "{\"status\":\"ok\",\"message\":\"No folders synced (${failed} failed out of ${folder_count})\",\"changed\":false,\"data\":{\"synced\":0,\"failed\":${failed},\"total\":${folder_count},\"dest\":\"${dest}\",\"details\":\"${sync_details}\"}}"
fi
