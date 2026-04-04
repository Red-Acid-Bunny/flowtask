#!/bin/bash
# flowtask bash module: smb_mount
# Монтирует SMB/CIFS шару через mount.cifs
#
# Input:
#   $1 (optional) — base64-encoded JSON (при become)
#   stdin — JSON payload (без become)
#
# Параметры:
#   server      — SMB-сервер (обязательный)
#   share       — имя шары (обязательный)
#   port        — TCP порт для проверки (по умолчанию 445)
#   mount_point — локальная точка монтирования (по умолчанию /mnt/smb)
#   user        — имя пользователя (пустой = guest)
#   password    — пароль
#   domain      — домен
#   version     — SMB версия (по умолчанию 3.0)
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

server=$(read_param "server" "")
share=$(read_param "share" "")
port=$(read_param "port" "445")
mount_point=$(read_param "mount_point" "/mnt/smb")
user=$(read_param "user" "")
password=$(read_param "password" "")
domain=$(read_param "domain" "")
version=$(read_param "version" "3.0")
dry_run=$(read_flag "dry_run" "false")

# =============================================
# Валидация
# =============================================
if [ -z "$server" ] || [ -z "$share" ]; then
  echo '{"status":"error","message":"server and share are required"}'
  exit 1
fi

# =============================================
# Проверка mount.cifs
# =============================================
if ! command -v mount.cifs &>/dev/null; then
  echo '{"status":"error","message":"mount.cifs not found. Install: sudo apt install cifs-utils"}'
  exit 1
fi

# =============================================
# Проверка доступности сервера (TCP)
# =============================================
>&2 echo "[INFO] Checking server $server:$port ..."
if ! python3 -c "
import socket, sys
try:
    s = socket.create_connection(('$server', $port), timeout=5)
    s.close()
    sys.exit(0)
except Exception as e:
    print(f'Connection failed: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
  echo "{\"status\":\"error\",\"message\":\"Server ${server}:${port} is not reachable\"}"
  exit 1
fi
>&2 echo "[INFO] Server $server:$port is reachable"

# =============================================
# Подготовка пути монтирования
# =============================================
mount_point=$(realpath -m "$mount_point" 2>/dev/null || echo "$mount_point")
mkdir -p "$mount_point"

# =============================================
# Проверка — уже смонтирована?
# =============================================
if grep -q "^//${server}/${share} ${mount_point} " /proc/mounts 2>/dev/null; then
  echo "{\"status\":\"ok\",\"message\":\"Already mounted: //${server}/${share} → ${mount_point}\",\"changed\":false,\"data\":{\"mount_point\":\"${mount_point}\",\"server\":\"${server}\",\"share\":\"${share}\"}}"
  exit 0
fi

# =============================================
# Dry run
# =============================================
if [ "$dry_run" = "true" ]; then
  echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would mount //${server}/${share} → ${mount_point}\",\"changed\":false,\"data\":{\"mount_point\":\"${mount_point}\",\"server\":\"${server}\",\"share\":\"${share}\"}}"
  exit 0
fi

# =============================================
# Формирование опций монтирования
# =============================================
opts="vers=${version},iocharset=utf8"

if [ -n "$user" ] && [ -n "$password" ]; then
  # Временный credentials-файл
  cred_file=$(mktemp /tmp/flowtask_cifs_XXXXXX)
  chmod 600 "$cred_file"
  echo "username=${user}" >"$cred_file"
  echo "password=${password}" >>"$cred_file"
  if [ -n "$domain" ]; then
    echo "domain=${domain}" >>"$cred_file"
  fi
  opts="${opts},credentials=${cred_file}"
elif [ -n "$user" ]; then
  opts="${opts},username=${user},guest"
  if [ -n "$domain" ]; then
    opts="${opts},domain=${domain}"
  fi
else
  opts="${opts},guest"
fi

# =============================================
# Cleanup trap
# =============================================
cleanup() {
  [ -n "${cred_file:-}" ] && [ -f "${cred_file:-}" ] && rm -f "$cred_file"
}
trap cleanup EXIT

# =============================================
# Монтирование
# =============================================
>&2 echo "[INFO] Mounting //${server}/${share} → ${mount_point} (vers=${version})"

mount_exit=0
sudo mount -t cifs "//${server}/${share}" "$mount_point" -o "$opts" >/dev/null 2>&1 || mount_exit=$?

if [ "$mount_exit" -ne 0 ]; then
  echo "{\"status\":\"error\",\"message\":\"Mount failed (exit code: ${mount_exit}). Check server, share, and credentials.\"}"
  exit 1
fi

>&2 echo "[INFO] Mounted successfully: //${server}/${share} → ${mount_point}"

echo "{\"status\":\"ok\",\"message\":\"Mounted //${server}/${share} → ${mount_point}\",\"changed\":true,\"data\":{\"mount_point\":\"${mount_point}\",\"server\":\"${server}\",\"share\":\"${share}\",\"version\":\"${version}\"}}"
