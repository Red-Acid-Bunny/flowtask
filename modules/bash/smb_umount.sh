#!/bin/bash
# flowtask bash module: umount_smb
# Unmount SMB/CIFS share
#
# Input (stdin): {"params": {"mount_point": "..."}, "dry_run": bool}
# Output (stdout): {"status": "ok|error", "message": "...", "changed": bool}

set -euo pipefail

input=$(cat)

mount_point=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('mount_point',''))")
lazy=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params'].get('lazy',False))")
dry_run=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('dry_run',False))")

if [ -z "$mount_point" ]; then
    echo '{"status":"error","message":"mount_point is required"}'
    exit 1
fi

>&2 echo "[INFO] Unmounting $mount_point"

if [ "$dry_run" = "True" ]; then
    echo "{\"status\":\"ok\",\"message\":\"[DRY-RUN] Would unmount $mount_point\",\"changed\":false}"
    exit 0
fi

if ! mountpoint -q "$mount_point" 2>/dev/null; then
    echo "{\"status\":\"ok\",\"message\":\"Not mounted: $mount_point\",\"changed\":false}"
    exit 0
fi

flag=""
if [ "$lazy" = "True" ]; then
    flag="-l"
    >&2 echo "[INFO] Using lazy unmount"
fi

if sudo umount $flag "$mount_point" 2>/tmp/flowtask_umnt_err; then
    echo "{\"status\":\"ok\",\"message\":\"Unmounted: $mount_point\",\"changed\":true,\"data\":{\"mount_point\":\"$mount_point\"}}"
else
    err=$(cat /tmp/flowtask_umnt_err 2>/dev/null | tr '"' "'" | head -1)
    echo "{\"status\":\"error\",\"message\":\"Unmount failed: ${err:-unknown error}\"}"
    exit 1
fi
