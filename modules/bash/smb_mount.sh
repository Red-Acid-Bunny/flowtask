#!/bin/bash
# flowtask bash module: smb_sync
# Монтирует SMB-ресурс (если не смонтирован) и синхронизирует папки через rsync
#
# Input (stdin): {"params": {...}, "dry_run": bool}
#   Параметры:
#     server      — SMB-сервер (обязательный)
#     share       — имя шары (обязательный)
#     path        — подпуть внутри шары (например "develop/V5-net6")
#     mount_point — локальная точка монтирования (по умолчанию /mnt/smb)
#     dest        — локальная директория назначения (обязательный)
#     folders     — список папок для выгрузки (JSON-массив строк)
#     excludes    — список исключений для rsync (JSON-массив строк)
#     user        — имя пользователя SMB
#     password    — пароль SMB
#     domain      — домен SMB
#     version     — версия SMB (по умолчанию 3.0)
# Output (stdout): {"status": "ok|error", "message": "...", "changed": bool, "data": {...}}
# Logs: stderr → перехватываются логгером FlowTask

set -euo pipefail

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

server=$(read_param "server" "")
share=$(read_param "share" "")
path=$(read_param "path" "")
mount_point=$(read_param "mount_point" "/mnt/smb")
dest=$(read_param "dest" "")
user=$(read_param "user" "")
password=$(read_param "password" "")
domain=$(read_param "domain" "")
version=$(read_param "version" "3.0")
dry_run=$(read_flag "dry_run" "false")

# folders и excludes — JSON-массивы
folders_json=$(echo "$input" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['params'].get('folders',[])))")
excludes_json=$(echo "$input" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['params'].get('excludes',[])))")

# =============================================
# Валидация
# =============================================
if [ -z "$server" ] || [ -z "$share" ]; then
    echo '{"status":"error","message":"server and share are required"}'
    exit 1
fi

if [ -z "$dest" ]; then
    echo '{"status":"error","message":"dest (destination directory) is required"}'
    exit 1
fi

# Проверяем, что folders не пустой
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
    echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would sync ${folder_count} folders from //${server}/${share}/${path} → ${dest}\",\"changed\":false,\"data\":{\"folders\":${folders_json},\"dest\":\"${dest}\"}}"
    exit 0
fi

>&2 echo "[INFO] Sync from //$server/$share/$path → $dest ($folder_count folders)"

# =============================================
# Монтирование (если не смонтировано)
# =============================================
mounted=false
if mountpoint -q "$mount_point" 2>/dev/null; then
    >&2 echo "[INFO] Already mounted: $mount_point"
    mounted=true
else
    # Проверяем cifs-utils
    if ! command -v mount.cifs &>/dev/null; then
        echo '{"status":"error","message":"mount.cifs not found. Install: sudo apt install cifs-utils"}'
        exit 1
    fi

    # Создаём точку монтирования
    sudo mkdir -p "$mount_point"

    # Формируем опции монтирования
    if [ -n "$user" ] && [ -n "$password" ]; then
        # Credentials file (единственный надёжный способ через sudo)
        cred_file=$(mktemp /tmp/flowtask_cifs_XXXXXX)
        echo "username=${user}" > "$cred_file"
        echo "password=${password}" >> "$cred_file"
        [ -n "$domain" ] && echo "domain=${domain}" >> "$cred_file"
        chmod 600 "$cred_file"

        opts="vers=${version},iocharset=utf8,credentials=${cred_file}"
    elif [ -n "$user" ]; then
        opts="vers=${version},iocharset=utf8,username=${user},guest"
        [ -n "$domain" ] && opts="${opts},domain=${domain}"
        cred_file=""
    else
        opts="vers=${version},iocharset=utf8,guest"
        cred_file=""
    fi

    >&2 echo "[INFO] Mounting //$server/$share → $mount_point"
    if sudo mount -t cifs "//$server/$share" "$mount_point" -o "$opts" 2>/tmp/flowtask_mnt_err; then
        mounted=true
        >&2 echo "[INFO] Mounted successfully"
    else
        err=$(cat /tmp/flowtask_mnt_err 2>/dev/null | tr '"' "'" | head -1)
        [ -n "${cred_file:-}" ] && rm -f "$cred_file"
        echo "{\"status\":\"error\",\"message\":\"Mount failed: ${err:-unknown error}\"}"
        exit 1
    fi

    # Удаляем credentials file
    [ -n "${cred_file:-}" ] && rm -f "$cred_file"
fi

# =============================================
# Rsync папок
# =============================================
src_base="${mount_point}/${path}"
synced=0
failed=0
sync_details=""

# Создаём директорию назначения
mkdir -p "$dest"

# Строим --exclude аргументы
exclude_args=""
exclude_count=$(echo "$excludes_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$exclude_count" -gt 0 ]; then
    while IFS= read -r excl; do
        exclude_args="$exclude_args --exclude='$excl'"
    done < <(echo "$excludes_json" | python3 -c "import sys,json; [print(x) for x in json.load(sys.stdin)]")
fi

>&2 echo "[INFO] Source base: $src_base"
>&2 echo "[INFO] Dest: $dest"

# Итерируемся по папкам
while IFS= read -r folder; do
    [ -z "$folder" ] && continue
    src_path="${src_base}/${folder}/"

    >&2 echo "[INFO] Syncing: $folder"

    if [ ! -d "$src_path" ]; then
        >&2 echo "[WARN] Source folder not found: $src_path — skipping"
        sync_details="${sync_details}${folder}:not_found "
        failed=$((failed + 1))
        continue
    fi

    # Выполняем rsync
    eval rsync -av --delete $exclude_args "$src_path" "$dest/" 2>&1 | while IFS= read -r line; do
        >&2 echo "[rsync] $line"
    done

    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        sync_details="${sync_details}${folder}:ok "
        synced=$((synced + 1))
    else
        sync_details="${sync_details}${folder}:error "
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
