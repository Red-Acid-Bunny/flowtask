#!/bin/bash
# flowtask bash module: smb_umount
# Размонтирует SMB/CIFS шару
#
# Input:
#   $1 (optional) — base64-encoded JSON (при become)
#   stdin — JSON payload (без become)
#
# Параметры:
#   mount_point — локальная точка монтирования (по умолчанию /mnt/smb)
#
# Output (stdout): {"status": "ok|error|skipped", "message": "...", "changed": bool, "data": {...}}
# Logs: stderr → перехватываются логгером FlowTask

set -eo pipefail

# =============================================
# Чтение параметров из JSON
# =============================================
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

mount_point=$(read_param "mount_point" "/mnt/smb")
dry_run=$(read_flag "dry_run" "false")

# =============================================
# Подготовка пути
# =============================================
mount_point=$(realpath -m "$mount_point" 2>/dev/null || echo "$mount_point")

# =============================================
# Проверка — смонтирована ли?
# Ищем точное совпадение mount_point в /proc/mounts
# =============================================
mount_info=$(grep " ${mount_point} " /proc/mounts 2>/dev/null || true)

if [ -z "$mount_info" ]; then
  echo "{\"status\":\"ok\",\"message\":\"Not mounted: ${mount_point}\",\"changed\":false,\"data\":{\"mount_point\":\"${mount_point}\"}}"
  exit 0
fi

# Извлекаем источник монтирования
mount_source=$(echo "$mount_info" | awk '{print $1}')

# =============================================
# Dry run
# =============================================
if [ "$dry_run" = "true" ]; then
  echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would unmount ${mount_point} (${mount_source})\",\"changed\":false,\"data\":{\"mount_point\":\"${mount_point}\",\"source\":\"${mount_source}\"}}"
  exit 0
fi

# =============================================
# Размонтирование
# =============================================
>&2 echo "[INFO] Unmounting ${mount_point} (${mount_source})"

umount_exit=0
if [ "$(id -u)" -eq 0 ]; then
  umount "$mount_point" >/dev/null 2>&1 || umount_exit=$?
else
  sudo umount "$mount_point" >/dev/null 2>&1 || umount_exit=$?
fi

if [ "$umount_exit" -ne 0 ]; then
  echo "{\"status\":\"error\",\"message\":\"Unmount failed (exit code: ${umount_exit}). Is the mount point busy?\"}"
  exit 1
fi

# Удаляем точку монтирования если пустая
rmdir "$mount_point" 2>/dev/null || true

>&2 echo "[INFO] Unmounted successfully: ${mount_point}"

echo "{\"status\":\"ok\",\"message\":\"Unmounted ${mount_point} (${mount_source})\",\"changed\":true,\"data\":{\"mount_point\":\"${mount_point}\",\"source\":\"${mount_source}\"}}"
