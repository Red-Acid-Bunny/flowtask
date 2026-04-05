# FlowTask

**Запускатор задач для файловых операций** — YAML-плейбуки с Python- и Bash-модулями.

FlowTask автоматизирует файловые операции (копирование, перемещение, удаление, архивация, монтирование/размонтирование SMB) с помощью декларативных YAML-плейбуков. Вдохновлён Ansible, но легче и сфокусирован на задачах управления файлами.

---

## Возможности

- **YAML-плейбуки** — объявляйте задачи в простом и читаемом формате
- **Подстановка переменных** — `{{ vars.key }}`, `{{ secrets.key }}`, `{{ today }}`
- **Двойная система модулей** — пишите модули на Python (нативные) или Bash (JSON-протокол)
- **Dry-run** — предпросмотр изменений без реального выполнения (`--dry-run`)
- **Идемпотентность** — безопасно запускать многократно, модули обнаруживают текущее состояние
- **Условное выполнение** — `when: success | failure | always | changed`
- **Регистрация результатов** — `register` сохраняет вывод задачи для использования в последующих
- **Фильтрация задач** — `--limit`, `--tags`, `--skip-tags`
- **Маскирование секретов** — пароли и токены никогда не выводятся в логах в открытом виде
- **Встроенные переменные** — `{{ today }}`, `{{ now }}`, `{{ timestamp }}`

---

## Быстрый старт

```bash
# Установка
pip install -e .

# Подготовка секретов
cp inventory/secrets.yml.example inventory/secrets.yml
# Отредактируйте inventory/secrets.yml и укажите реальные данные

# Запуск плейбука
flowtask run playbooks/deploy.yml

# Предпросмотр без выполнения
flowtask run playbooks/deploy.yml --dry-run

# Подробный вывод (DEBUG)
flowtask run playbooks/deploy.yml --verbose

# Запуск только конкретной задачи (по подстроке в имени)
flowtask run playbooks/deploy.yml --limit "mount"

# Запуск задач с определёнными тегами
flowtask run playbooks/deploy.yml --tags smb sync

# Валидация плейбука без выполнения
flowtask validate playbooks/deploy.yml

# Список доступных модулей
flowtask list-modules --verbose

# Версия
flowtask version
```

---

## Структура проекта

```
flowtask/
├── flowtask/                  # Движок и модули
│   ├── __init__.py            # Пакет (v0.1.0)
│   ├── cli.py                 # CLI — точка входа (argparse)
│   ├── engine/
│   │   ├── __init__.py        # Ленивые импорты, публичный API
│   │   ├── runner.py          # Оркестратор выполнения плейбуков
│   │   ├── context.py         # Загрузка переменных и секретов
│   │   ├── template.py        # Движок подстановки {{ }}
│   │   ├── result.py          # ModuleResult — контракт результатов
│   │   ├── module_loader.py   # Автообнаружение модулей
│   │   └── bash_adapter.py    # JSON-мост для Bash-модулей
│   └── modules/               # Встроенные Python-модули
│       ├── base.py            # BaseModule (ABC) + дескриптор @param
│       ├── copy.py            # Копирование файлов/директорий
│       ├── move.py            # Перемещение/переименование файлов
│       ├── delete.py          # Удаление файлов/директорий
│       ├── archive.py         # Создание архивов (zip, tar.gz, tar.xz)
├── modules/bash/              # Пользовательские Bash-модули
│   ├── smb_mount.sh           # Монтирование SMB (Bash)
│   └── smb_umount.sh          # Размонтирование SMB (Bash)
├── modules/python/            # Пользовательские Python-скрипты (JSON stdin/stdout)
├── inventory/
│   ├── vars.yml               # Переменные (коммитятся в репозиторий)
│   ├── vars.local.yml         # Локальные переопределения (gitignored)
│   ├── vars.local.yml.example # Шаблон локальных переопределений
│   ├── secrets.yml            # Секреты (gitignored)
│   └── secrets.yml.example    # Шаблон секретов
├── playbooks/
│   └── deploy.yml             # Пример плейбука выгрузки ПО
├── tests/                     # 197 тестов
│   ├── test_engine.py         # Context, Template, Result (35 тестов)
│   ├── test_modules.py        # BaseModule, Loader, Adapter (31 тест)
│   ├── test_builtin_modules.py# Встроенные модули (35 тестов)
│   ├── test_runner.py         # Runner, Playbook, условия (52 теста)
│   ├── test_cli.py            # CLI-команды (28 тестов)
│   └── test_integration.py    # E2E интеграционные тесты (16 тестов)
├── pyproject.toml             # Конфигурация сборки
└── README.md
```

---

## Справочник CLI

```
flowtask run <плейбук> [опции]

Опции:
  -n, --dry-run           Предпросмотр без выполнения
  -v, --verbose           Вывод уровня DEBUG
  -i, --inventory DIR     Переопределить каталог inventory
  -l, --limit ИМЯ        Выполнить только задачи, содержащие подстроку
  --tags ТЕГ [ТЕГ ...]   Выполнить только задачи с указанными тегами
  --skip-tags ТЕГ [ТЕГ]  Пропустить задачи с указанными тегами
  --continue-on-error     Продолжать выполнение после ошибок задач
  -K, --ask-become-pass   Запросить пароль sudo для become-задач

flowtask validate <плейбук> [-i DIR]     Проверить плейбук
flowtask list-modules [-v]               Список доступных модулей
flowtask version                         Версия
```

### Коды возврата

| Код | Значение |
|-----|----------|
| `0` | Успешное выполнение |
| `1` | Ошибка (в плейбуке, модуле, валидации) |
| `130` | Прервано пользователем (Ctrl+C) |

---

## Формат плейбука

```yaml
name: "Мой плейбук"
inventory: inventory/        # путь к каталогу inventory

vars:                        # переменные уровня плейбука (перекрывают inventory)
  env: "production"

pre_tasks:                   # задачи ДО основных (например, монтирование)
  - name: "Mount SMB"
    module: smb_mount
    become: true             # выполнить через sudo
    params:
      server: "192.168.0.8"
      share: "data"

tasks:
  - name: "Описание задачи"
    module: copy             # имя модуля
    params:                  # параметры модуля (поддерживается подстановка шаблонов)
      src: "/path/src"
      dest: "{{ vars.out_dir }}/{{ today }}/"
    when: success            # success | failure | always | changed | bool
    register: result_var     # сохранить результат в контекст
    ignore_errors: false     # продолжить при ошибке
    tags: [files, sync]      # для фильтрации --tags / --skip-tags

post_tasks:                  # задачи ПОСЛЕ основных (например, размонтирование)
  - name: "Unmount SMB"
    module: smb_umount
    become: true
    when: always             # всегда выполнять, даже при ошибках
```

### Параметры задачи

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `name` | строка | имя модуля | Отображаемое имя задачи |
| `module` | строка | **обязательный** | Модуль для выполнения |
| `params` | словарь | `{}` | Параметры, передаваемые в модуль |
| `when` | строка/bool | `None` | Условие: `success`, `failure`, `always`, `changed` или bool |
| `register` | строка | `None` | Сохранить результат в контекст под этим ключом |
| `ignore_errors` | bool | `false` | Продолжать выполнение плейбука при ошибке задачи |
| `tags` | список | `[]` | Теги для фильтрации `--tags` / `--skip-tags` |
| `become` | bool | `false` | Выполнить через sudo (только для Bash-модулей) |

### Секции задач

| Секция | Когда выполняется | Описание |
|--------|------------------|----------|
| `pre_tasks` | До `tasks` | Подготовка: монтирование, проверка зависимостей |
| `tasks` | Основная часть | Основные операции |
| `post_tasks` | После `tasks` | Очистка: размонтирование, архивация логов |

Порядок выполнения: `pre_tasks` → `tasks` → `post_tasks`

Если `pre_tasks` завершились с ошибкой, `tasks` пропускаются, но `post_tasks` выполняются.

### Условия выполнения (when)

Поле `when` определяет, будет ли задача выполнена, в зависимости от результата **предыдущей** задачи:

| Значение | Поведение |
|----------|-----------|
| `success` | Выполнить, если предыдущая задача завершилась успешно (для первой задачи — всегда) |
| `failure` | Выполнить только если предыдущая задача завершилась с ошибкой |
| `changed` | Выполнить только если предыдущая задача внесла изменения |
| `always` | Всегда выполнять, независимо от результата предыдущей задачи |
| `true` / не указано | Всегда выполнять |
| `false` | Никогда не выполнять |

---

## Встроенные модули

### copy

Копирование файлов и директорий. Поддерживает glob-паттерны.

```yaml
- name: "Копирование файлов"
  module: copy
  params:
    src: "/data/source/**"     # Обязательный. Путь или glob-паттерн
    dest: "/backup/"           # Обязательный. Куда копировать
    overwrite: true            # По умолчанию: true
    recursive: true            # По умолчанию: true
```

### move

Перемещение и переименование файлов и директорий. Поддерживает glob-паттерны.

```yaml
- name: "Перемещение логов"
  module: move
  params:
    src: "/app/logs/*.log"     # Обязательный. Путь или glob
    dest: "/archive/logs/"     # Обязательный. Куда переместить
    overwrite: false           # По умолчанию: false
```

### delete

Удаление файлов и директорий. Поддерживает glob-паттерны.

```yaml
- name: "Очистка временных файлов"
  module: delete
  params:
    path: "/tmp/cache/**"      # Обязательный. Путь или glob
    recursive: true            # По умолчанию: true
    force: true                # По умолчанию: false (игнорировать несуществующие)
```

### archive

Создание архивов в форматах zip, tar.gz или tar.xz.

```yaml
- name: "Создание резервной копии"
  module: archive
  params:
    src: "/data/backup/"       # Обязательный. Исходный путь
    format: "tar.gz"           # По умолчанию: "zip". Варианты: zip, tar.gz, tar.xz
    name: "backup_{{ today }}" # По умолчанию: автогенерация с меткой времени
    dest_dir: "/archives/"     # По умолчанию: родительский каталог src
```

### smb_mount

Монтирование SMB/CIFS-ресурса через `mount.cifs`.

```yaml
- name: "Монтирование SMB"
  module: smb_mount
  params:
    server: "{{ vars.smb_server }}"    # Обязательный. Например: "192.168.0.8"
    share: "{{ vars.smb_share }}"      # Обязательный. Например: "box_delta_bin"
    mount_point: "/mnt/smb"            # По умолчанию: "/mnt/smb"
    user: "{{ secrets.smb_user }}"     # По умолчанию: "" (гостевой доступ)
    password: "{{ secrets.smb_pass }}" # По умолчанию: ""
    domain: ""                         # По умолчанию: ""
    version: "3.0"                     # По умолчанию: "3.0"
```

### smb_umount

Размонтирование SMB/CIFS-ресурса.

```yaml
- name: "Размонтирование SMB"
  module: smb_umount
  params:
    mount_point: "/mnt/smb"   # По умолчанию: "/mnt/smb"
```

---

## Движок шаблонов

Шаблоны используют синтаксис `{{ }}` и разрешаются из контекста выполнения:

```
{{ vars.key }}          → из inventory/vars.yml (или vars.local.yml)
{{ secrets.key }}       → из inventory/secrets.yml
{{ today }}             → текущая дата (2026-04-03)
{{ now }}               → текущие дата/время (20260403_143000)
{{ timestamp }}         → unix-epoch (секунды)
{{ key }}               → автопоиск: builtins → vars → secrets
```

Секреты **маскируются** во всех логах — вместо реального значения отображается `***`. Это защищает пароли и токены от случайной утечки в консоль или файлы журнала.

---

## Inventory (инвентарь)

Каталог `inventory/` содержит конфигурационные файлы:

| Файл | В git | Описание |
|------|-------|----------|
| `vars.yml` | Да | Базовые переменные (адреса серверов, пути, списки папок) |
| `vars.local.yml` | **Нет** (.gitignore) | Локальные переопределения (специфичные для машины) |
| `secrets.yml` | **Нет** (.gitignore) | Секреты (пароли, токены) |
| `secrets.yml.example` | Да | Шаблон файла секретов |
| `vars.local.yml.example` | Да | Шаблон локальных переопределений |

### Порядок слияния переменных

Переменные объединяются каскадно — последний источник побеждает:

```
vars.yml → vars.local.yml → vars (уровня плейбука)
```

Глубокое слияние (deep merge) применяется для вложенных словарей: если оба файла содержат ключ `rsync_filter_excludes`, их списки объединяются, а не перезаписываются целиком.

Секреты загружаются отдельно из `secrets.yml` и доступны через пространство `secrets.*` в шаблонах.

---

## Написание собственных модулей

### Python-модуль

Создайте файл в `modules/` (или `flowtask/modules/` для встроенных модулей):

```python
from flowtask.modules.base import BaseModule, param
from flowtask.engine.result import ModuleResult

class MyModule(BaseModule):
    """Описание модуля."""

    name = "my_module"           # Опционально, автогенерация если пустое
    description = "Делает что-то полезное"

    # Объявление параметров через дескриптор @param
    src: str = param(required=True, help="Исходный путь")
    dest: str = param(default="/tmp/", help="Путь назначения")
    verbose: bool = param(default=False)

    def run(self) -> ModuleResult:
        """Выполнение модуля."""
        # self._dry_run и self._verbose доступны внутри
        # self._raw_params содержит исходный словарь параметров

        # ... логика ...

        return ModuleResult.changed("Готово", data={"items": 5})
```

Класс `BaseModule` обеспечивает:
- Автоматическую валидацию обязательных параметров через `validate_params()`
- Дескриптор `@param` с полями `required`, `default`, `help`
- Метод `execute()` с поддержкой dry-run (вызывается раннером)
- Свойство `param_schema` для получения схемы параметров (документация, CLI)
- Автоматическое маскирование параметров с `pass`/`secret` в логах

### Bash-модуль

Создайте `.sh` файл в `modules/bash/`:

```bash
#!/bin/bash
set -euo pipefail

# Чтение JSON из stdin
input=$(cat)
src=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['params']['src'])")
dry_run=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('dry_run',False))")

# Логи на stderr (перехватываются логгером FlowTask)
>&2 echo "[INFO] Обработка: $src"

# Dry-run режим
if [ "$dry_run" = "True" ]; then
    echo '{"status":"ok","message":"[DRY-RUN] Обработал бы '$src'","changed":false}'
    exit 0
fi

# ... выполнение ...

# JSON-результат на stdout
echo '{"status":"ok","message":"Обработано успешно","changed":true,"data":{"files":3}}'
```

#### JSON-протокол Bash-модулей

Каждый Bash-модуль взаимодействует с FlowTask через JSON:

| Направление | Формат | Описание |
|-------------|--------|----------|
| **stdin** | `{"params": {...}, "dry_run": bool, "verbose": bool}` | Входные параметры от раннера |
| **stdout** | `{"status": "ok\|error\|skipped", "message": "...", "changed": bool, "data": {...}}` | Результат выполнения |
| **stderr** | произвольный текст | Логи (передаются в логгер FlowTask) |

---

## Пример: полный плейбук с pre_tasks / post_tasks

```yaml
name: "Deploy — выгрузка с SMB"
inventory: inventory/

vars:
  out_dir: "{{ vars.out_base }}/{{ today }}"

pre_tasks:
  # 1. Монтирование SMB (требует sudo)
  - name: "Mount SMB share"
    module: smb_mount
    become: true
    params:
      server: "{{ vars.smb_server }}"
      share: "{{ vars.smb_share }}"

tasks:
  # 2. Синхронизация папок
  - name: "Sync folders from SMB"
    module: rsync
    params:
      src: "/mnt/smb/data"
      dest: "{{ vars.out_dir }}"
      folders: "{{ vars.download_folders }}"
    tags: [sync, download]
    register: sync_result

  # 3. Создание архива
  - name: "Create archive"
    module: archive
    params:
      src: "{{ vars.out_dir }}"
      format: "tar.gz"
    tags: [archive]
    when: success

post_tasks:
  # 4. Размонтирование (всегда, даже при ошибках)
  - name: "Unmount SMB share"
    module: smb_umount
    become: true
    when: always
```

### Privilege Escalation (become)

Для задач, требующих прав root (монтирование, системные операции), используйте `become: true`. Работает только с Bash-модулями.

```bash
# С запросом пароля
flowtask run playbook.yml --ask-become-pass

# Без запроса (если настроен NOPASSWD в sudoers)
flowtask run playbook.yml
```

**Безопасность пароля:**
- Пароль передаётся через pipe (`sudo -S`), не виден в `ps`
- Пароль НИКОГДА не логируется
- Пароль не сохраняется в контексте или переменных окружения
- Очищается сразу после выполнения задачи

**Настройка sudoers (опционально, для работы без пароля):**
```
username ALL=(ALL) NOPASSWD: /bin/bash /path/to/flowtask/modules/bash/*.sh
```

---

## Архитектура

FlowTask построен по модульной архитектуре с чётким разделением ответственности:

```
CLI (cli.py)
  │
  ▼
Runner (runner.py)  ◄── оркестратор, читает плейбук
  │
  ├──► Context (context.py)  ◄── загрузка vars + secrets + builtins
  │
  ├──► Template (template.py)  ◄── подстановка {{ }} в параметры
  │
  └──► ModuleLoader (module_loader.py)  ◄── автообнаружение модулей
        │
        ├──► Python-модуль (BaseModule subclass)  ◄── нативное выполнение
        │
        └──► BashModuleAdapter (bash_adapter.py)  ◄── JSON-протокол
             └──► bash-скрипт (stdin/stdout)
```

### Контракт результатов

Все модули возвращают `ModuleResult` — унифицированный результат выполнения:

| Поле | Тип | Описание |
|------|-----|----------|
| `status` | строка | `ok`, `error`, `skipped` |
| `message` | строка | Человекочитаемое описание |
| `changed` | bool | Были ли внесены изменения (идемпотентность) |
| `data` | словарь | Дополнительные данные для последующих задач |

---

## Разработка

```bash
# Установка в режиме разработки
pip install -e ".[dev]"

# Запуск всех тестов
pytest tests/ -v

# Запуск конкретного файла тестов
pytest tests/test_runner.py -v

# Запуск с покрытием кода
pytest tests/ --cov=flowtask

# Запуск только интеграционных тестов
pytest tests/test_integration.py -v
```

### Запуск тестов с фильтрами

```bash
# Только тесты с ключевым словом "template"
pytest tests/ -k template -v

# Только тесты с ключевым словом "copy"
pytest tests/ -k copy -v

# Остановка при первом падении
pytest tests/ -x
```

---

## Зависимости

- Python >= 3.10
- PyYAML >= 6.0

Опционально (для разработки):
- pytest >= 7.0
- pytest-cov >= 4.0

---

## Лицензия

MIT
