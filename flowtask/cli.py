"""
FlowTask CLI — точка входа для запуска плейбуков.

Команды:
  flowtask run <playbook>        Выполнить playbook
  flowtask validate <playbook>   Проверить playbook без выполнения
  flowtask list-modules          Показать доступные модули
  flowtask version               Версия

Usage:
  flowtask run playbook.yml --dry-run --verbose
  flowtask run deploy.yml --limit "mount" --tags smb
  flowtask validate playbook.yml
  flowtask list-modules
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__


def _setup_logging(verbose: bool = False) -> None:
    """Настроить логирование для CLI.

    Args:
        verbose: Подробный вывод (DEBUG вместо INFO)
    """
    level = logging.DEBUG if verbose else logging.INFO

    # Формат: время + уровень + логгер + сообщение
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Консольный handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(formatter)

    # Корневой логгер flowtask
    root = logging.getLogger("flowtask")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Не пускаем логи от других библиотек (если есть)
    logging.getLogger("").handlers.clear()
    logging.getLogger("").addHandler(handler)
    logging.getLogger("").setLevel(logging.WARNING)


# ============================================================
# Command handlers
# ============================================================

def cmd_run(args: argparse.Namespace) -> int:
    """Выполнить playbook."""
    from .engine.runner import Runner

    _setup_logging(verbose=args.verbose)

    runner = Runner(
        playbook_path=args.playbook,
        inventory_dir=args.inventory,
        dry_run=args.dry_run,
        verbose=args.verbose,
        stop_on_error=not args.continue_on_error,
        limit=args.limit,
        tags=args.tags,
        skip_tags=args.skip_tags,
    )

    result = runner.run()
    print(result.summary())

    return 0 if result.success else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Валидация playbook без выполнения."""
    from .engine.runner import Runner

    _setup_logging(verbose=False)

    runner = Runner(
        playbook_path=args.playbook,
        inventory_dir=args.inventory,
    )

    errors = runner.validate()

    if not errors:
        print(f"✓ Playbook '{args.playbook}' is valid")
        return 0
    else:
        print(f"✗ Playbook '{args.playbook}' has {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        return 1


def cmd_list_modules(args: argparse.Namespace) -> int:
    """Показать список доступных модулей."""
    from .engine.module_loader import ModuleLoader

    _setup_logging(verbose=args.verbose)

    loader = ModuleLoader()
    loader.discover()

    modules = loader.list_modules()

    if not modules:
        print("No modules found.")
        return 1

    # Группировка по типу
    python_modules = {k: v for k, v in modules.items() if v == "python"}
    bash_modules = {k: v for k, v in modules.items() if v == "bash"}

    if python_modules:
        print("Python modules:")
        for name in sorted(python_modules):
            # Показываем схему параметров если verbose
            if args.verbose:
                try:
                    cls = loader.get(name)
                    schema = cls.param_schema if hasattr(cls, "param_schema") else {}
                    params = ", ".join(
                        f"{p}{'*' if s.get('required') else '?'}"
                        for p, s in schema.items()
                    ) or "(no params)"
                    desc = getattr(cls, "description", "")
                    print(f"  {name:20s}  {params}  {desc}")
                except Exception:
                    print(f"  {name}")
            else:
                print(f"  {name}")

    if bash_modules:
        print("Bash modules:")
        for name in sorted(bash_modules):
            print(f"  {name}")

    print(f"\nTotal: {len(modules)} modules")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Показать версию."""
    print(f"FlowTask v{__version__}")
    return 0


# ============================================================
# Argument parser
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    """Создать парсер аргументов CLI."""
    parser = argparse.ArgumentParser(
        prog="flowtask",
        description="FlowTask — task runner for file operations with YAML playbooks",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"FlowTask v{__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- run ---
    run_parser = subparsers.add_parser(
        "run",
        help="Execute a playbook",
        description="Execute a YAML playbook with tasks",
    )
    run_parser.add_argument(
        "playbook",
        type=str,
        help="Path to playbook YAML file",
    )
    run_parser.add_argument(
        "--inventory", "-i",
        type=str,
        default=None,
        help="Path to inventory directory (overrides playbook setting)",
    )
    run_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        default=False,
        help="Preview without executing",
    )
    run_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Verbose output (DEBUG level)",
    )
    run_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Continue execution after task errors (unless ignore_errors is set)",
    )
    run_parser.add_argument(
        "--limit", "-l",
        type=str,
        default=None,
        help="Only run tasks matching this substring",
    )
    run_parser.add_argument(
        "--tags",
        type=str,
        nargs="*",
        default=None,
        help="Only run tasks with these tags",
    )
    run_parser.add_argument(
        "--skip-tags",
        type=str,
        nargs="*",
        default=None,
        help="Skip tasks with these tags",
    )
    run_parser.set_defaults(func=cmd_run)

    # --- validate ---
    val_parser = subparsers.add_parser(
        "validate",
        help="Validate a playbook without executing",
    )
    val_parser.add_argument(
        "playbook",
        type=str,
        help="Path to playbook YAML file",
    )
    val_parser.add_argument(
        "--inventory", "-i",
        type=str,
        default=None,
        help="Path to inventory directory",
    )
    val_parser.set_defaults(func=cmd_validate)

    # --- list-modules ---
    mod_parser = subparsers.add_parser(
        "list-modules",
        help="List available modules",
    )
    mod_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Show module parameters and descriptions",
    )
    mod_parser.set_defaults(func=cmd_list_modules)

    # --- version ---
    ver_parser = subparsers.add_parser(
        "version",
        help="Show version",
    )
    ver_parser.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Главная точка входа CLI.

    Args:
        argv: Аргументы командной строки (по умолчанию sys.argv[1:])

    Returns:
        Exit code (0 = success, 1 = error)
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        if "--verbose" in (argv or sys.argv[1:]):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
