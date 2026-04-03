# FlowTask

Task runner for file operations — YAML playbooks with Python and Bash modules.

## Features

- **YAML playbooks** — declare tasks in a simple format
- **Variable interpolation** — `{{ vars.key }}` and `{{ secrets.key }}`
- **Dual module system** — write modules in Python or Bash
- **JSON protocol** — language-agnostic module contract
- **Dry-run** — preview changes without execution
- **Idempotent** — safe to run multiple times

## Quick Start

```bash
pip install -e .

# Run a playbook
flowtask run playbooks/deploy.yml

# Dry-run (preview)
flowtask run playbooks/deploy.yml --dry-run

# Verbose output
flowtask run playbooks/deploy.yml --verbose
```

## Project Structure

```
flowtask/
├── flowtask/              # Engine (Python)
│   ├── cli.py             # CLI entry point
│   ├── engine/
│   │   ├── runner.py      # Task orchestrator
│   │   ├── context.py     # Variable/secrets loader
│   │   ├── template.py    # {{ }} interpolation
│   │   ├── module_loader.py
│   │   ├── bash_adapter.py
│   │   └── result.py
│   └── modules/           # Built-in Python modules
├── modules/bash/          # User Bash modules
├── inventory/
│   ├── vars.yml           # Variables
│   ├── secrets.yml        # Secrets (gitignored)
│   └── vars.local.yml     # Local overrides (gitignored)
├── playbooks/             # Task playbooks
└── tests/
```

## Playbook Example

```yaml
tasks:
  - name: "Mount SMB share"
    module: mount_smb
    server: "{{ vars.smb_server }}"
    share: "{{ vars.smb_share }}"

  - name: "Copy files"
    module: copy
    src: "{{ vars.smb_path }}/app/**"
    dest: "{{ vars.out_dir }}/"

  - name: "Archive"
    module: archive
    src: "{{ vars.out_dir }}/"
    format: zip
```

## Writing Modules

### Python module

```python
from flowtask.modules import BaseModule, ModuleResult, param

class MyModule(BaseModule):
    @param(required=True)
    src: str

    dest: str = "/tmp/"

    def run(self) -> ModuleResult:
        # ... do work
        return ModuleResult(status="ok", changed=True)
```

### Bash module

```bash
#!/bin/bash
# Read JSON from stdin
input=$(cat)
src=$(echo "$input" | jq -r '.params.src')

# Log to stderr
>&2 echo "[INFO] Processing $src"

# Return JSON to stdout
echo '{"status":"ok","changed":true}'
```

## License

MIT
