# FlowTask

**Task runner for file operations** — YAML playbooks with Python and Bash modules.

FlowTask automates file operations (copy, move, delete, archive, SMB mount/unmount) using declarative YAML playbooks. Inspired by Ansible but lightweight and focused on file management tasks.

## Features

- **YAML playbooks** — declare tasks in a simple, readable format
- **Variable interpolation** — `{{ vars.key }}`, `{{ secrets.key }}`, `{{ today }}`
- **Dual module system** — write modules in Python (native) or Bash (JSON protocol)
- **Dry-run** — preview changes without execution (`--dry-run`)
- **Idempotent** — safe to run multiple times, modules detect existing state
- **Conditional execution** — `when: success | failure | always | changed`
- **Result registration** — `register` saves task output for later tasks
- **Task filtering** — `--limit`, `--tags`, `--skip_tags`
- **Secret masking** — secrets are never printed in plain text in logs
- **Built-in variables** — `{{ today }}`, `{{ now }}`, `{{ timestamp }}`

## Quick Start

```bash
# Install
pip install -e .

# Prepare secrets
cp inventory/secrets.yml.example inventory/secrets.yml
# Edit inventory/secrets.yml with real values

# Run playbook
flowtask run playbooks/deploy.yml

# Preview without execution
flowtask run playbooks/deploy.yml --dry-run

# Verbose output
flowtask run playbooks/deploy.yml --verbose

# Run specific task only
flowtask run playbooks/deploy.yml --limit "mount"

# Run tasks with specific tags
flowtask run playbooks/deploy.yml --tags smb sync

# Validate playbook
flowtask validate playbooks/deploy.yml

# List available modules
flowtask list-modules --verbose

# Version
flowtask version
```

## Project Structure

```
flowtask/
├── flowtask/                  # Engine & modules
│   ├── __init__.py            # Package (v0.1.0)
│   ├── cli.py                 # CLI entry point (argparse)
│   ├── engine/
│   │   ├── __init__.py        # Lazy imports, public API
│   │   ├── runner.py          # Playbook orchestrator
│   │   ├── context.py         # Variable/secrets loader
│   │   ├── template.py        # {{ }} interpolation engine
│   │   ├── result.py          # ModuleResult dataclass
│   │   ├── module_loader.py   # Auto-discovery of modules
│   │   └── bash_adapter.py    # JSON bridge for Bash modules
│   └── modules/               # Built-in Python modules
│       ├── base.py            # BaseModule ABC + @param descriptor
│       ├── copy.py            # Copy files/directories
│       ├── move.py            # Move/rename files
│       ├── delete.py          # Delete files/directories
│       ├── archive.py         # Create archives (zip, tar.gz, tar.xz)
│       ├── mount_smb.py       # Mount SMB/CIFS share
│       └── umount_smb.py      # Unmount SMB/CIFS share
├── modules/bash/              # User Bash modules
│   ├── smb_mount.sh           # SMB mount (Bash)
│   └── smb_umount.sh          # SMB unmount (Bash)
├── inventory/
│   ├── vars.yml               # Variables (committed)
│   ├── vars.local.yml         # Local overrides (gitignored)
│   ├── vars.local.yml.example # Template for local overrides
│   ├── secrets.yml            # Secrets (gitignored)
│   └── secrets.yml.example    # Template for secrets
├── playbooks/
│   └── deploy.yml             # Deploy playbook example
├── tests/                     # 181 tests
│   ├── test_engine.py         # Context, Template, Result (35 tests)
│   ├── test_modules.py        # BaseModule, Loader, Adapter (31 tests)
│   ├── test_builtin_modules.py# Built-in modules (35 tests)
│   ├── test_runner.py         # Runner, Playbook, conditions (52 tests)
│   └── test_cli.py            # CLI commands (28 tests)
├── pyproject.toml             # Build config
└── README.md
```

## CLI Reference

```
flowtask run <playbook> [options]

Options:
  -n, --dry-run           Preview without executing
  -v, --verbose           DEBUG-level output
  -i, --inventory DIR     Override inventory directory
  -l, --limit NAME        Only run tasks matching substring
  --tags TAG [TAG ...]    Only run tasks with these tags
  --skip-tags TAG [TAG]   Skip tasks with these tags
  --continue-on-error     Continue after task errors

flowtask validate <playbook> [-i DIR]
flowtask list-modules [-v]
flowtask version
```

## Playbook Format

```yaml
name: "My Playbook"
inventory: inventory/        # path to inventory directory

vars:                        # playbook-level variables (override inventory)
  env: "production"

tasks:
  - name: "Task description"
    module: copy             # module name
    params:                  # module parameters (template support)
      src: "/path/src"
      dest: "{{ vars.out_dir }}/{{ today }}/"
    when: success            # success | failure | always | changed | bool
    register: result_var     # save result to context
    ignore_errors: false     # continue on error
    tags: [files, sync]      # for filtering
```

### Task Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | module name | Task display name |
| `module` | string | **required** | Module to execute |
| `params` | dict | `{}` | Parameters passed to module |
| `when` | string/bool | `None` | Conditional: `success`, `failure`, `always`, `changed`, or bool |
| `register` | string | `None` | Save result to context under this key |
| `ignore_errors` | bool | `false` | Continue playbook on task error |
| `tags` | list | `[]` | Tags for `--tags` / `--skip-tags` filtering |

### When Conditions

The `when` field controls whether a task executes based on the **previous** task's result:

- `success` — run only if previous task succeeded (default for first task)
- `failure` — run only if previous task failed
- `changed` — run only if previous task made changes
- `always` — always run regardless of previous result
- `true` / omitted — always run
- `false` — never run

## Built-in Modules

### copy

Copy files and directories. Supports glob patterns.

```yaml
- name: "Copy files"
  module: copy
  params:
    src: "/data/source/**"     # Required. Path or glob pattern
    dest: "/backup/"           # Required. Destination
    overwrite: true            # Default: true
    recursive: true            # Default: true
```

### move

Move/rename files and directories. Supports glob patterns.

```yaml
- name: "Move logs"
  module: move
  params:
    src: "/app/logs/*.log"     # Required. Path or glob
    dest: "/archive/logs/"     # Required. Destination
    overwrite: false           # Default: false
```

### delete

Delete files and directories. Supports glob patterns.

```yaml
- name: "Clean temp files"
  module: delete
  params:
    path: "/tmp/cache/**"      # Required. Path or glob
    recursive: true            # Default: true
    force: true                # Default: false (ignore nonexistent)
```

### archive

Create archives in zip, tar.gz, or tar.xz format.

```yaml
- name: "Create backup archive"
  module: archive
  params:
    src: "/data/backup/"       # Required. Source path
    format: "tar.gz"           # Default: "zip". Options: zip, tar.gz, tar.xz
    name: "backup_{{ today }}" # Default: auto-generated with timestamp
    dest_dir: "/archives/"     # Default: parent of src
```

### mount_smb

Mount SMB/CIFS share via `mount.cifs`.

```yaml
- name: "Mount SMB"
  module: mount_smb
  params:
    server: "{{ vars.smb_server }}"    # Required. e.g. "192.168.0.8"
    share: "{{ vars.smb_share }}"      # Required. e.g. "box_delta_bin"
    mount_point: "/mnt/smb"            # Default: "/mnt/smb"
    user: "{{ secrets.smb_user }}"     # Default: "" (guest)
    password: "{{ secrets.smb_pass }}" # Default: ""
    domain: ""                          # Default: ""
    version: "3.0"                      # Default: "3.0"
```

### umount_smb

Unmount SMB/CIFS share.

```yaml
- name: "Unmount SMB"
  module: umount_smb
  params:
    mount_point: "/mnt/smb"   # Required
    lazy: false               # Default: false. Use -l if busy
```

## Template Engine

Templates use `{{ }}` syntax and are resolved from context:

```
{{ vars.key }}          → from inventory/vars.yml
{{ secrets.key }}       → from inventory/secrets.yml
{{ today }}             → current date (2026-04-03)
{{ now }}               → current datetime (20260403_143000)
{{ timestamp }}         → unix epoch
{{ key }}               → auto-lookup: builtins → vars → secrets
```

Secrets are **masked** in all log output (shown as `***`).

## Inventory

The `inventory/` directory contains configuration files:

| File | Committed | Description |
|------|-----------|-------------|
| `vars.yml` | Yes | Base variables (server addresses, paths) |
| `vars.local.yml` | **No** (.gitignore) | Local overrides (machine-specific) |
| `secrets.yml` | **No** (.gitignore) | Secrets (passwords, tokens) |
| `secrets.yml.example` | Yes | Template for secrets |
| `vars.local.yml.example` | Yes | Template for local overrides |

Variables are merged: `vars.yml` → `vars.local.yml` (last wins). Playbook-level `vars` override both.

## Writing Custom Modules

### Python Module

Create a file in `modules/` (or `flowtask/modules/` for built-in):

```python
from flowtask.modules.base import BaseModule, param
from flowtask.engine.result import ModuleResult

class MyModule(BaseModule):
    """Module description."""

    name = "my_module"           # Optional, auto-generated if empty
    description = "Does something useful"

    # Parameters via @param descriptor
    src: str = param(required=True, help="Source path")
    dest: str = param(default="/tmp/", help="Destination")
    verbose: bool = param(default=False)

    def run(self) -> ModuleResult:
        """Execute the module."""
        # self._dry_run and self._verbose are available
        # self._raw_params contains original params dict

        # Do work...
        return ModuleResult.changed("Done", data={"items": 5})
```

### Bash Module

Create a `.sh` file in `modules/bash/`:

```bash
#!/bin/bash
set -euo pipefail

# Read JSON from stdin
input=$(cat)
src=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params']['src'])")
dry_run=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('dry_run',False))")

# Log to stderr (captured by FlowTask logger)
>&2 echo "[INFO] Processing: $src"

# Dry run
if [ "$dry_run" = "True" ]; then
    echo '{"status":"ok","message":"[DRY-RUN] Would process '$src'","changed":false}'
    exit 0
fi

# Do work...

# Return JSON result on stdout
echo '{"status":"ok","message":"Processed successfully","changed":true,"data":{"files":3}}'
```

**Protocol:**
- **stdin**: `{"params": {...}, "dry_run": bool, "verbose": bool}`
- **stdout**: `{"status": "ok|error|skipped", "message": "...", "changed": bool, "data": {...}}`
- **stderr**: forwarded to FlowTask logger

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_runner.py -v

# Run with coverage
pytest tests/ --cov=flowtask
```

## License

MIT
