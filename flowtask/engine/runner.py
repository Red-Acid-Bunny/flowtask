"""
Runner — оркестратор выполнения playbook.

Читает YAML-плейбук, загружает контекст, выполняет задачи последовательно.

Playbook format:
  name: "Deploy software"
  inventory: inventory/
  vars:
    key: value
  pre_tasks:
    - name: "Mount SMB"
      module: smb_mount
      become: true
      params:
        server: "{{ vars.smb_server }}"
  tasks:
    - name: "Sync files"
      module: rsync
      params:
        src: "/mnt/smb/data"
        dest: "/backup/"
  post_tasks:
    - name: "Unmount SMB"
      module: smb_umount
      become: true
      when: always

Features:
  - Шаблонизация параметров через Template engine
  - Условия выполнения: when (success/failure/always/changed)
  - Регистрация результатов: register → сохраняет в context
  - Пропуск ошибок: ignore_errors
  - Dry-run и verbose режимы
  - Детальный отчёт выполнения
  - Ограничение задач: limit (по имени или индексу)
  - Tag-фильтрация: tags / skip_tags
  - Privilege escalation: become (только для Bash-модулей)
  - Секции задач: pre_tasks → tasks → post_tasks
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context import Context
from .template import Template
from .result import ModuleResult, ModuleError
from .module_loader import ModuleLoader, ModuleNotFoundError

logger = logging.getLogger("flowtask.runner")


# ============================================================
# Playbook model
# ============================================================

@dataclass
class TaskDef:
    """Определение задачи из playbook."""
    name: str
    module: str
    params: dict[str, Any] = field(default_factory=dict)
    when: str | bool | None = None
    register: str | None = None
    ignore_errors: bool = False
    tags: list[str] = field(default_factory=list)
    loop: list[Any] | None = None
    become: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> TaskDef:
        """Создать TaskDef из словаря playbook."""
        return cls(
            name=data.get("name", data.get("module", "unnamed")),
            module=data["module"],
            params=data.get("params", {}),
            when=data.get("when"),
            register=data.get("register"),
            ignore_errors=data.get("ignore_errors", False),
            tags=data.get("tags", []),
            loop=data.get("loop"),
            become=data.get("become", False),
        )


@dataclass
class Playbook:
    """Загруженный playbook."""
    name: str
    inventory: str = "inventory/"
    vars: dict[str, Any] = field(default_factory=dict)
    pre_tasks: list[TaskDef] = field(default_factory=list)
    tasks: list[TaskDef] = field(default_factory=list)
    post_tasks: list[TaskDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Playbook:
        """Создать Playbook из словаря."""
        return cls(
            name=data.get("name", "Unnamed playbook"),
            inventory=data.get("inventory", "inventory/"),
            vars=data.get("vars", {}),
            pre_tasks=[TaskDef.from_dict(t) for t in data.get("pre_tasks", [])],
            tasks=[TaskDef.from_dict(t) for t in data.get("tasks", [])],
            post_tasks=[TaskDef.from_dict(t) for t in data.get("post_tasks", [])],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> Playbook:
        """Загрузить playbook из YAML-файла."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Playbook not found: {p}")

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid playbook format in {p}: expected dict, got {type(data).__name__}")

        playbook = cls.from_dict(data)
        playbook._source_path = p
        return playbook


# ============================================================
# Task result (execution record)
# ============================================================

@dataclass
class TaskRecord:
    """Запись о выполнении одной задачи."""
    index: int
    name: str
    module: str
    status: str = "pending"
    changed: bool = False
    message: str = ""
    duration: float = 0.0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    section: str = "tasks"  # pre_tasks | tasks | post_tasks

    @property
    def is_success(self) -> bool:
        return self.status in ("ok", "changed")

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"


# ============================================================
# Playbook result (execution summary)
# ============================================================

@dataclass
class PlaybookResult:
    """Итоговый результат выполнения playbook."""
    name: str
    status: str = "ok"
    total: int = 0
    ok: int = 0
    changed: int = 0
    skipped: int = 0
    failed: int = 0
    duration: float = 0.0
    records: list[TaskRecord] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "ok" and self.failed == 0

    def summary(self) -> str:
        """Краткое резюме выполнения."""
        lines = [
            f"\n{'='*60}",
            f"  PLAYBOOK: {self.name}",
            f"{'='*60}",
        ]
        for rec in self.records:
            icon = {
                "ok": "OK",
                "changed": "CHANGED",
                "skipped": "SKIPPED",
                "error": "FAILED",
            }.get(rec.status, "???")
            dur = f"{rec.duration:.2f}s"
            section_tag = f"[{rec.section}]" if rec.section != "tasks" else "       "
            lines.append(
                f"  [{icon:>8}] {section_tag} {rec.index+1}. {rec.name} ({dur})"
            )
            if rec.is_error and rec.error:
                lines.append(f"             └─ {rec.error}")

        lines.append(f"{'─'*60}")
        lines.append(
            f"  Total: {self.total}  OK: {self.ok}  Changed: {self.changed}  "
            f"Skipped: {self.skipped}  Failed: {self.failed}  "
            f"Time: {self.duration:.2f}s"
        )
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


# ============================================================
# Runner
# ============================================================

class PlaybookError(Exception):
    """Ошибка в playbook (формат, валидация)."""
    pass


class Runner:
    """Оркестратор выполнения playbook.

    Usage:
        runner = Runner(
            playbook_path="playbooks/deploy.yml",
            dry_run=False,
            verbose=True,
        )
        result = runner.run()
        print(result.summary())

    Features:
        - Автоматическое обнаружение модулей через ModuleLoader
        - Шаблонизация параметров через Template
        - Условное выполнение задач (when)
        - Регистрация результатов (register)
        - Фильтрация задач по имени (limit), тегам (tags, skip_tags)
        - Глобальный stop_on_error
        - Privilege escalation: become (только Bash-модули)
        - Секции: pre_tasks → tasks → post_tasks
    """

    def __init__(
        self,
        playbook_path: str | Path,
        inventory_dir: str | Path | None = None,
        dry_run: bool = False,
        verbose: bool = False,
        stop_on_error: bool = True,
        limit: str | None = None,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        extra_modules_dirs: list[str | Path] | None = None,
        become_pass: str | None = None,
    ):
        """
        Args:
            playbook_path: Путь к YAML-плейбуку
            inventory_dir: Путь к inventory (переопределяет playbook.inventory)
            dry_run: Предпросмотр без выполнения
            verbose: Подробный лог
            stop_on_error: Остановить при первой ошибке
            limit: Выполнить только задачу с совпадающим именем (подстрока)
            tags: Выполнить только задачи с указанными тегами
            skip_tags: Пропустить задачи с указанными тегами
            extra_modules_dirs: Дополнительные директории с модулями
            become_pass: Пароль sudo для become (передаётся через pipe)
        """
        self._playbook_path = Path(playbook_path)
        self._inventory_dir = inventory_dir
        self._dry_run = dry_run
        self._verbose = verbose
        self._stop_on_error = stop_on_error
        self._limit = limit
        self._tags = set(tags) if tags else None
        self._skip_tags = set(skip_tags) if skip_tags else None
        self._extra_modules_dirs = extra_modules_dirs
        self._become_pass = become_pass

        self._playbook: Playbook | None = None
        self._context: Context | None = None
        self._template: Template | None = None
        self._loader: ModuleLoader | None = None
        self._prev_result: ModuleResult | None = None

    # --- Основной API ---

    def run(self) -> PlaybookResult:
        """Выполнить playbook и вернуть результат.

        Порядок выполнения: pre_tasks → tasks → post_tasks
        """
        start_time = time.monotonic()

        # 1. Загрузка playbook
        self._playbook = Playbook.from_file(self._playbook_path)
        total_tasks = (
            len(self._playbook.pre_tasks)
            + len(self._playbook.tasks)
            + len(self._playbook.post_tasks)
        )
        logger.info("Loaded playbook: %s (%d tasks)", self._playbook.name, total_tasks)

        # 2. Загрузка контекста
        inv_dir = self._inventory_dir or self._playbook.inventory
        self._context = Context.from_inventory(inv_dir)

        # 3. Template engine
        self._template = Template(self._context)

        # Применяем playbook-level vars
        if self._playbook.vars:
            for key, value in self._playbook.vars.items():
                rendered = self._template.render_any(value)
                self._context.set(key, rendered)
            logger.debug("Applied playbook vars: %d keys", len(self._playbook.vars))

        # 4. Module loader
        self._loader = ModuleLoader(extra_modules_dirs=self._extra_modules_dirs)
        discovered = self._loader.discover()
        logger.info("Modules discovered: %s", discovered)

        # 5. Выполнение секций: pre_tasks → tasks → post_tasks
        records: list[TaskRecord] = []
        pre_tasks_failed = False
        tasks_failed = False

        sections = [
            ("pre_tasks", self._playbook.pre_tasks),
            ("tasks", self._playbook.tasks),
            ("post_tasks", self._playbook.post_tasks),
        ]

        task_index = 0
        for section_name, section_tasks in sections:
            if not section_tasks:
                continue

            # Если pre_tasks провалились → пропускаем tasks (но выполняем post_tasks)
            if section_name == "tasks" and pre_tasks_failed:
                logger.info("Skipping tasks due to pre_tasks failure")
                continue

            filtered = self._filter_tasks(section_tasks)
            logger.info("Executing %s: %d/%d tasks", section_name, len(filtered), len(section_tasks))

            section_failed = False
            for task_def in filtered:
                record = self._execute_task(task_index, task_def, section=section_name)
                records.append(record)
                task_index += 1

                self._prev_result = ModuleResult(
                    status=record.status,
                    changed=record.changed,
                    message=record.message,
                    data=record.data,
                )

                if record.is_error:
                    section_failed = True
                    if self._stop_on_error and not task_def.ignore_errors:
                        logger.error("Stopping on error in task: %s", task_def.name)
                        break

            if section_name == "pre_tasks" and section_failed:
                pre_tasks_failed = True
            if section_name == "tasks" and section_failed:
                tasks_failed = True

        playbook_failed = pre_tasks_failed or tasks_failed

        # 6. Подсчёт результатов
        duration = time.monotonic() - start_time
        result = PlaybookResult(
            name=self._playbook.name,
            status="error" if playbook_failed else "ok",
            total=len(records),
            ok=sum(1 for r in records if r.status == "ok"),
            changed=sum(1 for r in records if r.status == "changed"),
            skipped=sum(1 for r in records if r.is_skipped),
            failed=sum(1 for r in records if r.is_error),
            duration=duration,
            records=records,
        )

        return result

    # --- Внутренние методы ---

    def _filter_tasks(self, tasks: list[TaskDef]) -> list[TaskDef]:
        """Фильтрация задач по limit, tags, skip_tags."""
        filtered = tasks

        if self._limit:
            limit_lower = self._limit.lower()
            filtered = [t for t in filtered if limit_lower in t.name.lower()]
            if not filtered:
                logger.warning("Limit '%s' matched no tasks", self._limit)

        if self._tags is not None:
            filtered = [t for t in filtered if self._tags & set(t.tags)]
            if not filtered:
                logger.warning("Tags %s matched no tasks", self._tags)

        if self._skip_tags is not None:
            filtered = [t for t in filtered if not (self._skip_tags & set(t.tags))]

        return filtered

    def _should_run(self, task_def: TaskDef) -> tuple[bool, str]:
        """Определить, должна ли задача выполниться."""
        when = task_def.when

        if when is None or when is True:
            return True, ""

        if when is False:
            return False, "when=false"

        if isinstance(when, str):
            when_lower = when.strip().lower()

            if when_lower == "always":
                return True, ""

            if when_lower == "success":
                if self._prev_result is None:
                    return True, ""
                if not self._prev_result.is_error and not self._prev_result.is_skipped:
                    return True, ""
                return False, f"when=success (prev status: {self._prev_result.status})"

            if when_lower == "failure":
                if self._prev_result is None:
                    return False, "when=failure (no previous task)"
                if self._prev_result.is_error:
                    return True, ""
                return False, f"when=failure (prev status: {self._prev_result.status})"

            if when_lower == "changed":
                if self._prev_result is None:
                    return False, "when=changed (no previous task)"
                if self._prev_result.changed:
                    return True, ""
                return False, "when=changed (prev not changed)"

            if self._template:
                rendered = self._template.render(when)
                if rendered == when:
                    pass
                try:
                    val = yaml.safe_load(rendered)
                    if isinstance(val, bool):
                        if val:
                            return True, ""
                        return False, f"when={when} (evaluated to false)"
                except (yaml.YAMLError, ValueError):
                    pass

        return True, ""

    def _execute_task(
        self, index: int, task_def: TaskDef, section: str = "tasks"
    ) -> TaskRecord:
        """Выполнить одну задачу."""
        record = TaskRecord(
            index=index,
            name=task_def.name,
            module=task_def.module,
            section=section,
        )

        become_tag = " [become]" if task_def.become else ""
        logger.info("─── Task %d: %s (module: %s)%s ───", index + 1, task_def.name, task_def.module, become_tag)

        should_run, skip_reason = self._should_run(task_def)
        if not should_run:
            record.status = "skipped"
            record.message = skip_reason
            logger.info("Skipped: %s — %s", task_def.name, skip_reason)
            return record

        record.status = "running"
        start_time = time.monotonic()

        try:
            module_ref = self._loader.get(task_def.module)
            rendered_params = self._template.render_any(task_def.params) if self._template else task_def.params
            record.params = rendered_params

            if isinstance(module_ref, type) and hasattr(module_ref, '__bases__'):
                # Python-модуль (become не поддерживается)
                if task_def.become:
                    logger.warning("become is not supported for Python modules, ignoring for task: %s", task_def.name)
                instance = module_ref(**rendered_params)
                result = instance.execute(dry_run=self._dry_run, verbose=self._verbose)
            else:
                # Bash-модуль
                result = module_ref.execute(
                    params=rendered_params,
                    dry_run=self._dry_run,
                    verbose=self._verbose,
                    become=task_def.become,
                    become_pass=self._become_pass if task_def.become else None,
                )

            record.status = "changed" if result.changed else ("ok" if result.is_ok else result.status)
            record.changed = result.changed
            record.message = result.message
            record.data = result.data

            if self._verbose:
                logger.info("Result: %s", result)

            if task_def.register and self._context:
                self._context.set(task_def.register, {
                    "status": result.status,
                    "changed": result.changed,
                    "message": result.message,
                    "data": result.data,
                })
                logger.debug("Registered: %s", task_def.register)

        except ModuleNotFoundError as e:
            record.status = "error"
            record.error = str(e)
            record.message = str(e)
            logger.error("Module not found for task '%s': %s", task_def.name, e)

        except ModuleError as e:
            record.status = "error"
            record.error = str(e)
            record.message = str(e)
            logger.error("Module error in task '%s': %s", task_def.name, e)

        except Exception as e:
            record.status = "error"
            record.error = f"{type(e).__name__}: {e}"
            record.message = f"{type(e).__name__}: {e}"
            logger.error("Unexpected error in task '%s': %s", task_def.name, e, exc_info=True)

        finally:
            record.duration = time.monotonic() - start_time

        return record

    # --- Утилиты ---

    @property
    def playbook(self) -> Playbook | None:
        return self._playbook

    @property
    def context(self) -> Context | None:
        return self._context

    def validate(self) -> list[str]:
        """Валидация playbook без выполнения."""
        errors = []

        try:
            self._playbook = Playbook.from_file(self._playbook_path)
        except (FileNotFoundError, ValueError) as e:
            return [f"Playbook load error: {e}"]

        # Проверка всех секций
        sections = {
            "pre_tasks": self._playbook.pre_tasks,
            "tasks": self._playbook.tasks,
            "post_tasks": self._playbook.post_tasks,
        }

        for section_name, section_tasks in sections.items():
            for i, task in enumerate(section_tasks):
                if not task.name:
                    errors.append(f"{section_name}[{i}]: missing name")
                if not task.module:
                    errors.append(f"{section_name}[{i}]: missing module")

        # Проверка inventory
        inv_dir = self._inventory_dir or self._playbook.inventory
        inv_path = Path(inv_dir)
        if not inv_path.exists():
            errors.append(f"Inventory directory not found: {inv_dir}")

        # Проверка модулей
        self._loader = ModuleLoader(extra_modules_dirs=self._extra_modules_dirs)
        self._loader.discover()
        for section_name, section_tasks in sections.items():
            for i, task in enumerate(section_tasks):
                if not self._loader.has(task.module):
                    errors.append(f"{section_name}[{i}] ({task.name}): module '{task.module}' not found")

        return errors

    def __repr__(self) -> str:
        return (
            f"Runner(playbook={self._playbook_path!r}, "
            f"dry_run={self._dry_run}, verbose={self._verbose})"
        )
