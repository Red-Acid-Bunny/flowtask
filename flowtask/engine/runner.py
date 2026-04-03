"""
Runner — оркестратор выполнения playbook.

Читает YAML-плейбук, загружает контекст, выполняет задачи последовательно.

Playbook format:
  name: "Deploy software"
  inventory: inventory/
  vars:
    key: value
  tasks:
    - name: "Mount SMB"
      module: mount_smb
      params:
        server: "{{ vars.smb_server }}"
        password: "{{ secrets.smb_pass }}"
      when: success          # success | failure | always | changed | <bool>
      register: mount_result
      ignore_errors: false

Features:
  - Шаблонизация параметров через Template engine
  - Условия выполнения: when (success/failure/always/changed)
  - Регистрация результатов: register → сохраняет в context
  - Пропуск ошибок: ignore_errors
  - Dry-run и verbose режимы
  - Детальный отчёт выполнения
  - Ограничение задач: limit (по имени или индексу)
  - Tag-фильтрация: tags / skip_tags
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
    when: str | bool | None = None      # success | failure | always | changed | bool
    register: str | None = None          # сохранить результат в context
    ignore_errors: bool = False
    tags: list[str] = field(default_factory=list)
    loop: list[Any] | None = None        # итерация по списку (stretch)

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
        )


@dataclass
class Playbook:
    """Загруженный playbook."""
    name: str
    inventory: str = "inventory/"
    vars: dict[str, Any] = field(default_factory=dict)
    tasks: list[TaskDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Playbook:
        """Создать Playbook из словаря."""
        tasks = [TaskDef.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            name=data.get("name", "Unnamed playbook"),
            inventory=data.get("inventory", "inventory/"),
            vars=data.get("vars", {}),
            tasks=tasks,
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
    status: str = "pending"    # pending | running | ok | changed | skipped | error
    changed: bool = False
    message: str = ""
    duration: float = 0.0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

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
    status: str = "ok"           # ok | error | partial
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
            lines.append(
                f"  [{icon:>8}] {rec.index+1}. {rec.name} ({dur})"
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

        # Внутреннее состояние
        self._playbook: Playbook | None = None
        self._context: Context | None = None
        self._template: Template | None = None
        self._loader: ModuleLoader | None = None
        self._prev_result: ModuleResult | None = None

    # --- Основной API ---

    def run(self) -> PlaybookResult:
        """Выполнить playbook и вернуть результат.

        Returns:
            PlaybookResult с детальными записями по каждой задаче
        """
        start_time = time.monotonic()

        # 1. Загрузка playbook
        self._playbook = Playbook.from_file(self._playbook_path)
        logger.info("Loaded playbook: %s (%d tasks)", self._playbook.name, len(self._playbook.tasks))

        # 2. Загрузка контекста
        inv_dir = self._inventory_dir or self._playbook.inventory
        self._context = Context.from_inventory(inv_dir)
        # Применяем playbook-level vars (перекрывают inventory)
        if self._playbook.vars:
            for key, value in self._playbook.vars.items():
                self._context.set(key, value)
            logger.debug("Applied playbook vars: %d keys", len(self._playbook.vars))

        # 3. Template engine
        self._template = Template(self._context)

        # 4. Module loader
        self._loader = ModuleLoader(extra_modules_dirs=self._extra_modules_dirs)
        discovered = self._loader.discover()
        logger.info("Modules discovered: %s", discovered)

        # 5. Фильтрация задач
        tasks = self._filter_tasks(self._playbook.tasks)
        logger.info("Tasks to execute: %d/%d", len(tasks), len(self._playbook.tasks))

        # 6. Выполнение
        records: list[TaskRecord] = []
        playbook_failed = False

        for i, task_def in enumerate(tasks):
            record = self._execute_task(i, task_def)
            records.append(record)

            # Обновляем предыдущий результат (для when-условий)
            self._prev_result = ModuleResult(
                status=record.status,
                changed=record.changed,
                message=record.message,
                data=record.data,
            )

            if record.is_error:
                playbook_failed = True
                if self._stop_on_error and not task_def.ignore_errors:
                    logger.error("Stopping on error in task: %s", task_def.name)
                    break

        # 7. Подсчёт результатов
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

        # Выводим отчёт
        logger.info(result.summary())
        return result

    # --- Внутренние методы ---

    def _filter_tasks(self, tasks: list[TaskDef]) -> list[TaskDef]:
        """Фильтрация задач по limit, tags, skip_tags."""
        filtered = tasks

        # Limit по имени (подстрока)
        if self._limit:
            limit_lower = self._limit.lower()
            filtered = [t for t in filtered if limit_lower in t.name.lower()]
            if not filtered:
                logger.warning("Limit '%s' matched no tasks", self._limit)

        # Фильтр по tags (если указаны — только задачи с тегами)
        if self._tags is not None:
            filtered = [t for t in filtered if self._tags & set(t.tags)]
            if not filtered:
                logger.warning("Tags %s matched no tasks", self._tags)

        # skip_tags
        if self._skip_tags is not None:
            filtered = [t for t in filtered if not (self._skip_tags & set(t.tags))]

        return filtered

    def _should_run(self, task_def: TaskDef) -> tuple[bool, str]:
        """Определить, должна ли задача выполниться.

        Returns:
            (should_run, reason) — флаг и причина пропуска
        """
        when = task_def.when

        # None / True → всегда выполнять
        if when is None or when is True:
            return True, ""

        # False → пропустить
        if when is False:
            return False, "when=false"

        # Строковые условия
        if isinstance(when, str):
            when_lower = when.strip().lower()

            if when_lower == "always":
                return True, ""

            if when_lower == "success":
                if self._prev_result is None:
                    return True, ""  # первая задача → success
                if self._prev_result.is_ok:
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

            # Попытка шаблонизации (например "vars.some_flag")
            if self._template:
                rendered = self._template.render(when)
                if rendered == when:
                    # Шаблон не заменился — может быть литерал
                    pass
                try:
                    val = yaml.safe_load(rendered)
                    if isinstance(val, bool):
                        if val:
                            return True, ""
                        return False, f"when={when} (evaluated to false)"
                except (yaml.YAMLError, ValueError):
                    pass

        # По умолчанию — выполнять
        return True, ""

    def _execute_task(self, index: int, task_def: TaskDef) -> TaskRecord:
        """Выполнить одну задачу.

        Returns:
            TaskRecord с результатом выполнения
        """
        record = TaskRecord(
            index=index,
            name=task_def.name,
            module=task_def.module,
        )

        logger.info("─── Task %d: %s (module: %s) ───", index + 1, task_def.name, task_def.module)

        # Проверка when-условия
        should_run, skip_reason = self._should_run(task_def)
        if not should_run:
            record.status = "skipped"
            record.message = skip_reason
            logger.info("Skipped: %s — %s", task_def.name, skip_reason)
            return record

        record.status = "running"
        start_time = time.monotonic()

        try:
            # 1. Получить модуль
            module_ref = self._loader.get(task_def.module)

            # 2. Рендеринг параметров
            rendered_params = self._template.render_any(task_def.params) if self._template else task_def.params
            record.params = rendered_params

            # 3. Выполнение
            if isinstance(module_ref, type) and hasattr(module_ref, '__bases__'):
                # Python-модуль (BaseModule subclass)
                instance = module_ref(**rendered_params)
                result = instance.execute(dry_run=self._dry_run, verbose=self._verbose)
            else:
                # Bash-модуль (BashModuleAdapter)
                result = module_ref.execute(
                    params=rendered_params,
                    dry_run=self._dry_run,
                    verbose=self._verbose,
                )

            # 4. Обновить запись
            record.status = "changed" if result.changed else ("ok" if result.is_ok else result.status)
            record.changed = result.changed
            record.message = result.message
            record.data = result.data

            if self._verbose:
                logger.info("Result: %s", result)

            # 5. Регистрация результата
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
        """Текущий playbook (после run())."""
        return self._playbook

    @property
    def context(self) -> Context | None:
        """Текущий контекст (после run())."""
        return self._context

    def validate(self) -> list[str]:
        """Валидация playbook без выполнения.

        Returns:
            Список ошибок/предупреждений (пустой = всё ОК)
        """
        errors = []

        # Загрузка playbook
        try:
            self._playbook = Playbook.from_file(self._playbook_path)
        except (FileNotFoundError, ValueError) as e:
            return [f"Playbook load error: {e}"]

        # Проверка задач
        for i, task in enumerate(self._playbook.tasks):
            if not task.name:
                errors.append(f"Task {i}: missing name")
            if not task.module:
                errors.append(f"Task {i}: missing module")

        # Проверка inventory
        inv_dir = self._inventory_dir or self._playbook.inventory
        inv_path = Path(inv_dir)
        if not inv_path.exists():
            errors.append(f"Inventory directory not found: {inv_dir}")

        # Проверка модулей
        self._loader = ModuleLoader(extra_modules_dirs=self._extra_modules_dirs)
        self._loader.discover()
        for i, task in enumerate(self._playbook.tasks):
            if not self._loader.has(task.module):
                errors.append(f"Task {i} ({task.name}): module '{task.module}' not found")

        return errors

    def __repr__(self) -> str:
        return (
            f"Runner(playbook={self._playbook_path!r}, "
            f"dry_run={self._dry_run}, verbose={self._verbose})"
        )
