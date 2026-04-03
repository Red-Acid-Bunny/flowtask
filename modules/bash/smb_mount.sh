#!/bin/bash
# flowtask bash module: mount_smb
# Mount SMB/CIFS share via mount.cifs
#
# Input (stdin): {"params": {"server": "...", "share": "...", ...}, "dry_run": bool}
# Output (stdout): {"status": "ok|error", "message": "...", "changed": bool, "data": {...}}

set -euo pipefail

input=$(cat)

server=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('server',''))")
share=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('share',''))")
mount_point=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point','/mnt/smb'))")
version=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('version','3.0'))")
user=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('user',''))")
password=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('password',''))")
domain=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('domain',''))")
dry_run=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('dry_run',False))")

# Validation
if [ -z "$server" ] || [ -z "$share" ]; then
    echo '{"status":"error","message":"server and share are required"}'
    exit 1
fi

>&2 echo "[INFO] Mounting //$server/$share → $mount_point"

# Dry run
if [ "$dry_run" = "True" ]; then
    echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would mount //$server/$share → $mount_point\",\"changed\":false,\"data\":{\"mount_point\":\"$mount_point\"}}"
    exit 0
fi

# Already mounted?
if mountpoint -q "$mount_point" 2>/dev/null; then
    echo "{\"status\":\"ok\",\"message\":\"Already mounted: $mount_point\",\"changed\":false,\"data\":{\"mount_point\":\"$mount_point\"}}"
    exit 0
fi

# Check cifs-utils
if ! command -v mount.cifs &>/dev/null; then
    echo '{"status":"error","message":"mount.cifs not found. Install: sudo apt install cifs-utils"}'
    exit 1
fi

# Build options
opts="vers=${version},iocharset=utf8,guest"
if [ -n "$user" ]; then
    opts="vers=${version},iocharset=utf8,username=${user}"
    [ -n "$domain" ] && opts="${opts},domain=${domain}"
fi

# Create mount point
sudo mkdir -p "$mount_point"

# Mount
export PASSWD="${password}"
if sudo mount -t cifs "//$server/$share" "$mount_point" -o "$opts" 2>/tmp/flowtask_mnt_err; then
    echo "{\"status\":\"ok\",\"message\":\"Mounted //$server/$share → $mount_point\",\"changed\":true,\"data\":{\"mount_point\":\"$mount_point\",\"server\":\"$server\",\"share\":\"$share\"}}"
else
    err=$(cat /tmp/flowtask_mnt_err 2>/dev/null | tr '"' "'" | head -1)
    echo "{\"status\":\"error\",\"message\":\"Mount failed: ${err:-unknown error}\"}"
    exit 1
fi
