# КОНСОЛЬ: подбор программ, позиций и ключей — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** КОНСОЛЬ узнаёт имя программы, что ждёт каждая позиция (путь,
каталог, подкоманда, хост) и ключи утилит у машины — bash-completion, man,
`--help`, — а не из таблиц в исходниках.

**Architecture:** Новый модуль `complete.py` спрашивает bash-completion так же,
как Tab (отдельный `bash` на запрос, 20–50 мс), и отдаёт вид позиции и
кандидатов. В `shell.py` новый шаг `_fit` проходит по токенам после `_glue` и
подбирает каждый под свою позицию нечётким выбором `_closest` (обобщение
`_match_loose`). Таблица `WORDS` сжимается до исключений, до которых подбор
не дотягивается.

**Tech Stack:** Python 3 (stdlib: subprocess, difflib), bash 5 +
bash-completion 2.16, man-db.

**Spec:** `docs/superpowers/specs/2026-10-04-shell-discovery-design.md`

## Global Constraints

- Язык кода, комментариев, строк журнала, коммитов — русский. Тема коммита:
  `Консоль: что изменилось`, тело — зачем, списком. В конце тела:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- В рабочем дереве лежат незакоммиченные правки пользователя в `voiced.py`.
  `voiced.py` не трогать и не коммитить: каждый `git commit` — с явным
  списком файлов (`git commit -m … -- файл1 файл2`).
- Таймауты: запрос к completion `TIMEOUT = 1.0` с; man, `--help` и разбор
  справки `HELP_TIMEOUT = 2.0` с.
- Пороги нечёткого выбора: программы `PROGRAM_FIT = (0.78, 0.10)`,
  подкоманды/кандидаты/ключи `WORD_FIT = (0.80, 0.15)` (порог, отрыв).
  Точное совпадение после транслита (единственное) берётся сразу.
- `--help` запускается только у программы из PATH (`shutil.which`, без `/`
  в имени), с `stdin=/dev/null`, `LC_ALL=C`, в своём сеансе процессов;
  по таймауту группа убивается.
- Журнал приватен: кандидаты позиций (хосты, файлы, ветки, службы) и пути
  каталогов пользователя в журнал не пишутся. В сообщениях о сбоях — только
  имя программы из PATH, вид сбоя, `strerror` без `filename`.
- `LETTERS`, `SYMBOLS`, `NUMBER_WORDS`, `SYNONYMS` не трогать.
- Проверки — только в `sandbox()` из `test_shell.py`, без настоящих каталогов
  пользователя; подставные имена (`atlasctl`, `riverctl`, `harborctl`,
  `deploy-staging`), не реальные.
- Запуск тестов: `PY=~/.local/share/nohands/venv/bin/python; $PY test_shell.py`
  — успех печатает `ИТОГ: ПРОЙДЕН`. Отдельная функция: `$PY -m pytest
  test_shell.py -k <имя> -q`. До плана набор проходил за 0,66 с; после задачи
  3 будет дольше (bash на каждую позицию) — это ожидаемо.

## Review Focus

- **Слово намерения похоже на программу.** «сделай» (0.71), «плавать» (0.73
  с pacat) — первое слово должно остаться как сказано, и фраза уйти в LLM.
  Тест — задача 3, «не программа».
- **Каталога терминала больше нет** (удалили, пока терминал открыт). Фраза
  собирается, исключения нет. Тест — задача 2 («каталога нет»), задача 3
  («каталога терминала нет»).
- **Ключ одним минусом** («минус вербоус» -> `-вербоус`). Ожидается
  `--verbose`. Тест — задача 3.
- **Длинная фраза без completion** («эхо …» из семи слов) — семь запросов
  по 30 мс; фраза должна собираться быстрее 2 с. Тест — задача 3.
- **Повторный сбой** (completion завис, `--help` завис) не забивает журнал:
  одно сообщение на процесс. Тест — задача 2.

---

## Файлы

| Файл | Что меняется |
|---|---|
| `complete.py` (новый) | `programs()`, `position()`, `options()`, `note()`, `drain()`; bash-скрипт запроса |
| `shell.py` | `SOUNDS` + `ктл`; `_closest`; `_match_loose` через `_closest`; `_fit` и помощники; `rules`, `runnable`, `to_command`; `BUILTINS` удаляется; `WORDS` сжимается |
| `test_shell.py` | `test_closest`, `standins()`, `test_complete`, `test_fit`; вызовы в `main()` |
| `AGENTS.md` | строка `complete.py` в таблице Layout |
| `README.md` | абзац о подборе программ, подкоманд и ключей в разделе КОНСОЛИ |

---

### Task 1: `_closest` — нечёткий выбор из списка имён

**Files:**
- Modify: `shell.py:185-186` (`SOUNDS`), `shell.py:509-525` (`_match_loose`)
- Test: `test_shell.py` (новая `test_closest`, вызов в `main()`)

**Interfaces:**
- Produces: `shell._closest(said: str, names: Iterable[str], cutoff: float, lead: float, stem=lambda n: n, variants: bool = False) -> tuple[str | None, float]`
  — `(имя, сходство)` или `(None, 0.0)`.
- Produces: `shell.english("системктл") == "sistemctl"` (правило `ктл -> ctl`).

- [ ] **Step 1: Написать падающий тест**

В `test_shell.py` после `test_runnable` добавить:

```python
def test_closest():
    print("── нечёткий выбор ──")
    check("точное после транслита", S._closest("гит", ["git", "gitk", "gedit"], 0.78, 0.1),
          ("git", 1.0))
    names = ["docker", "dockerd", "done", "gedit"]
    # docker 0.91, dockerd 0.83: отрыв 0.08, а от done — 0.24
    check("вариант того же имени — не соперник",
          S._closest("докер", names, 0.78, 0.1, variants=True)[0], "docker")
    check("  без variants отрыв мал", S._closest("докер", names, 0.78, 0.1), (None, 0.0))
    check("ниже порога — нет", S._closest("покажи", ["pkaction", "pkill"], 0.78, 0.1),
          (None, 0.0))
    stem = lambda n: os.path.splitext(n)[0]
    check("две равные основы — не точное",
          S._closest("ноутс", ["notes.txt", "notes.md"], 0.6, 0.1, stem=stem), (None, 0.0))
    check("ктл — это ctl", S.english("системктл"), "sistemctl")
```

В `main()` после `test_runnable()` добавить строку `    test_closest()`.

- [ ] **Step 2: Убедиться, что падает**

Run: `~/.local/share/nohands/venv/bin/python -m pytest test_shell.py -k closest -q`
Expected: FAIL — `AttributeError: module 'shell' has no attribute '_closest'`.

- [ ] **Step 3: Реализация**

В `shell.py` в `SOUNDS` дописать правило и комментарий над ним:

```python
# Английские окончания, которые транслит по буквам не узнаёт: «фьюзжен» ->
# fyuzzhen, похоже на fusion на 0.43 при пороге 0.7; с заменой — 0.92.
# «ктл» — ctl у systemctl, resolvectl, kubectl: «системктл» похож на
# systemctl на 0.78, с заменой — на 0.89; «кубектл» — 1.00 вместо 0.86.
SOUNDS = [("зжен", "жен"), ("жен", "sion"), ("жн", "sion"), ("шен", "tion"),
          ("шн", "tion"), ("ктл", "ctl")]
```

Тело `_match_loose` заменить вызовом `_closest`, а сам `_closest` поставить
сразу после `_match_loose`:

```python
def _match_loose(said, base, kind, cutoff, lead):
    """Лучшее имя нужного вида (файл, каталог) со сходством от cutoff, если
    оно обгоняет второе хотя бы на lead: (имя, сходство) или (None, 0)."""
    try:
        names = [n for n in os.listdir(base) if kind(os.path.join(base, n))]
    except OSError:
        return None, 0.0
    stem = (lambda n: os.path.splitext(n)[0]) if kind is os.path.isfile else (lambda n: n)
    return _closest(said, names, cutoff, lead, stem=stem)


def _closest(said, names, cutoff, lead, stem=lambda n: n, variants=False):
    """Имя из names, похожее на сказанное: (имя, сходство) или (None, 0).

    Совпало точно (после транслита, без «-_.») и такое имя одно — берём.
    Иначе лучшее не ниже cutoff и обгоняет следующее на lead. С variants
    следующее — не вариант лучшего: docker против dockerd — отрыв 0.08,
    против настоящего соперника done — 0.24 (замер 04.10)."""
    bare = lambda s: re.sub(r"[-_.]", "", s.lower())
    probes = {bare(p) for p in (said, translit(said), translit(said, ks=True), english(said))}
    keys = [(bare(stem(n)), n) for n in names]
    exact = [n for k, n in keys if k in probes]
    if len(exact) == 1:
        return exact[0], 1.0
    # Имя ниже cutoff - lead не помешает ни как лучшее, ни как соперник, а
    # quick_ratio — верхняя граница ratio и много дешевле: программ 3586.
    floor = cutoff - lead
    scored = []
    for k, n in keys:
        best = 0.0
        for p in probes:
            m = difflib.SequenceMatcher(None, p, k)
            if m.real_quick_ratio() >= floor and m.quick_ratio() >= floor:
                best = max(best, m.ratio())
        if best >= floor:
            scored.append((best, k, n))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < cutoff:
        return None, 0.0
    top, key, name = scored[0]
    rivals = [s for s, k, _ in scored[1:] if not (variants and k.startswith(key))]
    if rivals and top - rivals[0] < lead:
        return None, 0.0
    return name, top
```

- [ ] **Step 4: Убедиться, что проходит, и что старое не сломано**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py 2>&1 | grep -E "FAIL|ИТОГ"`
Expected: `ИТОГ: ПРОЙДЕН`, строк `FAIL` нет.

- [ ] **Step 5: Коммит**

```bash
git commit -m "Консоль: нечёткий выбор из списка имён — _closest

- _match_loose теперь частный случай: файлы и каталоги, а дальше —
  программы, подкоманды и ключи
- варианты того же имени (dockerd у docker) не считаются соперником
- ктл -> ctl: «системктл» 0.78 -> 0.89, «кубектл» 0.86 -> 1.00

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- shell.py test_shell.py
```

---

### Task 2: `complete.py` — что ждёт позиция, у bash-completion

**Files:**
- Create: `complete.py`
- Modify: `AGENTS.md` (таблица Layout)
- Test: `test_shell.py` (`standins()`, `test_complete`, вызов в `main()`)

**Interfaces:**
- Produces (все функции исключений наружу не бросают):
  - `complete.programs() -> frozenset[str]` — имена из `compgen -c`.
  - `complete.position(words: list[str], cwd: str) -> tuple[str, list[str]] | None`
    — вид `"files" | "dirs" | "words" | "program"` и отсортированные
    кандидаты без пробелов по краям; `None` — узнать не удалось.
  - `complete.options(words: list[str]) -> list[str]` — ключи команды
    (`words` от имени программы), отсортированы; `--x=` с «=» на конце.
  - `complete.note(msg: str)`, `complete.drain() -> list[str]` — сообщения
    о сбоях, каждое один раз на процесс.
  - Константы `COMPLETION`, `TIMEOUT = 1.0`, `HELP_TIMEOUT = 2.0`.
- Test helper produces: `standins() -> (каталог, env)`; рабочий каталог
  `<каталог>/work` с `deploy-staging/` и `notes.txt`; программы в
  `<каталог>/bin`: `atlasctl` (completion: `deploy rollback status
  --verbose --dry-run`; после `deploy` — `filedir -d`, иначе `filedir`),
  `riverctl` (man: `--verbose`, `--follow`), `harborctl` (`--help`:
  `--quiet`, `--region=`), `riverportd`, `slowctl` (completion спит 30 с),
  `slowhelp` (`--help` спит 30 с).

- [ ] **Step 1: Написать падающий тест**

В `test_shell.py`: в строку импорта добавить `import complete as C` отдельной
строкой после `import shell as S`. После `test_closest` добавить:

```python
ATLAS_COMPLETION = r'''
_atlasctl() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    if ((COMP_CWORD == 1)); then
        COMPREPLY=($(compgen -W "deploy rollback status --verbose --dry-run" -- "$cur"))
    elif [[ ${COMP_WORDS[1]} == deploy ]]; then
        _comp_compgen -a filedir -d
    else
        _comp_compgen -a filedir
    fi
}
complete -F _atlasctl atlasctl
'''
SLOW_COMPLETION = "_slowctl() { sleep 30; }\ncomplete -F _slowctl slowctl\n"
RIVER_MAN = r""".TH RIVERCTL 1
.SH NAME
riverctl \- stand-in
.SH OPTIONS
.TP
\fB\-v\fR, \fB\-\-verbose\fR
more output
.TP
\fB\-\-follow\fR
keep reading
"""
HARBOR = r"""#!/bin/sh
[ "$1" = --help ] && printf 'Usage: harborctl [OPTION]\n  -q, --quiet         be quiet\n      --region=NAME   region\n'
"""


def standins():
    """Подставные программы с известными completion, man и --help: подбор
    проверяется на них, а не на том, что стоит на машине.
    -> (каталог, окружение для mock.patch.dict); рабочий каталог — <каталог>/work."""
    d = sandbox("bin", "compl/completions", "man/man1", "work/deploy-staging",
                files=["work/notes.txt"])
    bins = {"atlasctl": "#!/bin/sh\n", "riverctl": "#!/bin/sh\n",
            "riverportd": "#!/bin/sh\n", "slowctl": "#!/bin/sh\n",
            "harborctl": HARBOR, "slowhelp": "#!/bin/sh\nsleep 30\n"}
    for name, body in bins.items():
        path = os.path.join(d, "bin", name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)
    for path, body in [("compl/completions/atlasctl", ATLAS_COMPLETION),
                       ("compl/completions/slowctl", SLOW_COMPLETION),
                       ("man/man1/riverctl.1", RIVER_MAN)]:
        with open(os.path.join(d, path), "w") as f:
            f.write(body)
    return d, {"PATH": os.path.join(d, "bin") + os.pathsep + os.environ["PATH"],
               "BASH_COMPLETION_USER_DIR": os.path.join(d, "compl"),
               "MANPATH": os.path.join(d, "man")}


def test_complete():
    print("── что ждёт позиция (complete) ──")
    d, env = standins()
    work = os.path.join(d, "work")
    with mock.patch.dict(os.environ, env):
        C.drain()
        check("программа из PATH", "atlasctl" in C.programs(), True)
        check("встроенная — тоже программа", "cd" in C.programs(), True)
        check("подкоманды из completion", C.position(["atlasctl"], work),
              ("words", ["--dry-run", "--verbose", "deploy", "rollback", "status"]))
        check("filedir -d — каталог", C.position(["atlasctl", "deploy"], work)[0], "dirs")
        check("filedir — файл", C.position(["atlasctl", "status"], work)[0], "files")
        check("своего completion нет — файл", C.position(["riverctl"], work), ("files", []))
        check("после sudo — имя программы", C.position(["sudo"], work)[0], "program")
        check("у cd — каталог", C.position(["cd"], work)[0], "dirs")
        check("каталога нет — не знаю", C.position(["atlasctl"], os.path.join(d, "gone")), None)
        check("ключи из completion", C.options(["atlasctl"]), ["--dry-run", "--verbose"])
        check("ключи из man", C.options(["riverctl"]), ["--follow", "--verbose"])
        check("ключи из --help", C.options(["harborctl"]), ["--quiet", "--region="])
        t = time.time()
        check("--help завис — ключей нет", C.options(["slowhelp"]), [])
        check("  ждали не дольше таймаута", time.time() - t < C.HELP_TIMEOUT + 1, True)
        t = time.time()
        check("completion завис — не знаю", C.position(["slowctl"], work), None)
        check("  ждали не дольше таймаута", time.time() - t < C.TIMEOUT + 1, True)
        msgs = C.drain()
        check("  оба сбоя — в журнал",
              [any("slowhelp" in m for m in msgs), any("slowctl" in m for m in msgs)],
              [True, True])
        C.position(["slowctl"], work)
        check("  тот же сбой второй раз не пишется", C.drain(), [])
        with mock.patch.object(C, "COMPLETION", os.path.join(d, "missing")):
            check("нет bash-completion — не знаю", C.position(["atlasctl"], work), None)
        check("  и об этом одна строка", len(C.drain()), 1)
```

В `main()` после `test_closest()` — строка `    test_complete()`.

- [ ] **Step 2: Убедиться, что падает**

Run: `~/.local/share/nohands/venv/bin/python -m pytest test_shell.py -k complete -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'complete'`.

- [ ] **Step 3: Реализация — `complete.py`**

```python
"""Что ждёт команда на следующей позиции — так, как это знает Tab.

Имена программ, подкоманды, хосты, службы, места для путей и ключи берутся у
установленного bash-completion, man и `--help`, а не из таблиц в исходниках.
Модуль не знает ни о демоне, ни о звуке, ни о правилах КОНСОЛИ: на входе
слова и каталог. Исключений наружу не бросает — копит сообщения о сбоях,
их забирает drain().
"""

import os, shutil, signal, subprocess

COMPLETION = "/usr/share/bash-completion/bash_completion"
# Запрос к completion — 20–50 мс (git, systemctl --user, apt), docker compose
# — 0.24 с, pytest -- — 0.41 с: он сам зовёт pytest --help (замер 04.10).
TIMEOUT = 1.0
# man, --help чужой программы и разбор справки: man curl (5049 строк)
# разбирается за 0.36 с. Не ответила за 2 с — значит, делает что-то другое.
HELP_TIMEOUT = 2.0
KINDS = ("files", "dirs", "words")

# Скрипт для bash. Режим «words»: COMP_WORDS — слова, последнее — текущее;
# печатает вид позиции и кандидатов по строке. Режим «help»: разбирает
# справку со stdin парсером самого bash-completion — свой разбор форматов
# man и --help был бы ещё одной таблицей правил.
SCRIPT = r'''
source "$NOHANDS_COMPLETION" >/dev/null 2>&1 || exit 3
mode=$1; shift
if [[ $mode == help ]]; then
    COMPREPLY=(); cur=
    _comp_compgen -v COMPREPLY help - <&0 2>/dev/null
    printf '%s\n' "${COMPREPLY[@]}"
    exit 0
fi
# Путь на позиции скрипт completion ищет через filedir: перехват и говорит,
# ждут ли там файл или каталог (-d).
KIND=
for _f in _comp_compgen_filedir _comp_compgen_filedir_xspec; do
    eval "_nohands_orig$_f() $(declare -f $_f | tail -n +2)"
    eval "$_f() { [[ \" \$* \" == *' -d '* ]] && KIND=dirs || KIND=\${KIND:-files}; _nohands_orig$_f \"\$@\"; }"
done
COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
COMP_LINE="$*"; COMP_POINT=${#COMP_LINE}; COMPREPLY=()
_comp_complete_load "$1" >/dev/null 2>&1
spec=$(complete -p "$1" 2>/dev/null)
fn=$(sed -n 's/.* -F \([^ ]*\).*/\1/p' <<<"$spec")
# _comp_complete_minimal — своего completion нет, Tab предложит файлы
if [[ $fn && $fn != _comp_complete_minimal ]]; then
    "$fn" "$1" "${COMP_WORDS[COMP_CWORD]}" "${COMP_WORDS[COMP_CWORD-1]}" >/dev/null 2>&1 </dev/null
    echo "${KIND:-words}"
else
    echo files
fi
printf '%s\n' "${COMPREPLY[@]}"
'''

_errors, _told = [], set()


def note(msg):
    """Сбой — в журнал один раз на процесс: фраз в КОНСОЛИ много, а причина
    у них одна."""
    if msg not in _told:
        _told.add(msg)
        _errors.append(msg)


def drain():
    """Новые сообщения о сбоях с прошлого вызова."""
    got = _errors[:]
    del _errors[:]
    return got


def _run(argv, timeout, cwd=None, stdin=None, env=None, merge=False):
    """(код, вывод) или None по таймауту. Свой сеанс процессов: по таймауту
    убивается вся группа — completion git и --help чужой программы
    запускают своих детей, и те пережили бы убитый bash. После kill только
    wait, без чтения: ребёнок, ушедший в свой сеанс, держал бы трубу вечно."""
    p = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                         encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT if merge else subprocess.DEVNULL)
    try:
        out, _ = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
        return None
    return p.returncode, out


def _bash(mode, args, cwd, stdin=None, timeout=TIMEOUT):
    """Строки вывода SCRIPT или None."""
    if not os.path.isdir(cwd):      # терминал в удалённом каталоге — не сбой
        return None
    env = {**os.environ, "NOHANDS_COMPLETION": COMPLETION}
    who = args[0] if args and os.sep not in args[0] else mode
    try:
        got = _run(["bash", "-c", SCRIPT, "bash", mode, *args], timeout,
                   cwd=cwd, stdin=stdin, env=env)
    except OSError as ex:
        # без ex.filename: там каталог пользователя, а журнал копится месяцами
        note(f"bash не запустился: {ex.strerror}")
        return None
    if got is None:
        note(f"completion {who}: нет ответа за {timeout} с")
        return None
    if got[0] == 3:
        note(f"нет {COMPLETION}: позиции и ключи не подбираются")
        return None
    return got[1].splitlines()


def _mtime(d):
    try:
        return os.stat(d).st_mtime
    except OSError:
        return None


_programs = (None, frozenset())


def programs():
    """Имена, которые bash запустит: PATH, встроенные, ключевые слова.
    compgen -c — 90 мс на 3586 имён, поэтому кеш — пока не поменялись PATH и
    его каталоги (mtime каталога меняется при установке программы)."""
    global _programs
    path = os.environ.get("PATH", "")
    key = (path, tuple(_mtime(d) for d in path.split(os.pathsep)))
    if _programs[0] != key:
        try:
            got = _run(["bash", "-c", "compgen -c"], TIMEOUT)
        except OSError as ex:
            note(f"bash не запустился: {ex.strerror}")
            got = None
        names = frozenset(got[1].split()) if got and got[0] == 0 else frozenset()
        _programs = (key, names)
    return _programs[1]


def position(words, cwd):
    """Что ждут сразу после words (от начала команды): (вид, кандидаты) или
    None. Вид — files, dirs, words или program: кандидаты покрывают не
    меньше половины programs(), как после sudo, env, time, — так позиция
    имени программы узнаётся без перечня этих утилит."""
    lines = _bash("words", [*words, ""], cwd)
    if not lines or lines[0] not in KINDS:
        return None
    # у git кандидаты с пробелом на конце: «add »
    kind, cands = lines[0], sorted({c.strip() for c in lines[1:] if c.strip()})
    if kind == "words" and not cands:
        kind = "files"          # Tab без кандидатов предлагает файлы
    names = programs()
    if kind == "words" and names and 2 * len(names.intersection(cands)) >= len(names):
        kind = "program"
    return kind, cands


_options = {}


def options(words):
    """Ключи команды words (от имени программы: ["git", "commit"]). По
    порядку, до первого, где нашлись: completion, man, --help. Кеш на время
    жизни процесса: справка от фразы к фразе не меняется."""
    key = tuple(words[:2])
    if key not in _options:
        _options[key] = _find_options(list(key))
    return _options[key]


def _find_options(words):
    home = os.path.expanduser("~")
    got = set()
    for cur in ("--", "-"):
        lines = _bash("words", [*words, cur], home)
        if lines and lines[0] == "words":
            got |= {c.strip() for c in lines[1:] if c.strip().startswith("-")}
    if got:
        return sorted(got)
    prog = words[0]
    pages = [f"{prog}-{words[1]}"] if len(words) > 1 and words[1].isalpha() else []
    for page in pages + [prog]:
        found = _parse(_man(page))
        if found:
            return found
    return _parse(_help(prog))


def _man(page):
    """Текст man-страницы или ''. man только читает файл — программа не
    запускается."""
    env = {**os.environ, "MANWIDTH": "1000", "LC_ALL": "C", "MANPAGER": "cat",
           "PAGER": "cat"}
    try:
        got = _run(["man", page], HELP_TIMEOUT, env=env)
    except OSError:
        return ""
    return got[1] if got and got[0] == 0 else ""


def _help(prog):
    """Вывод `prog --help` или ''. Только программа из PATH: свой скрипт из
    каталога (./create.riverport.sh) мог бы на --help выполнить действие."""
    path = shutil.which(prog) if os.sep not in prog else None
    if not path:
        return ""
    try:
        got = _run([path, "--help"], HELP_TIMEOUT, env={**os.environ, "LC_ALL": "C"},
                   merge=True)
    except OSError:
        return ""
    if got is None:
        note(f"{prog} --help: нет ответа за {HELP_TIMEOUT} с")
        return ""
    return got[1]


def _parse(text):
    """Ключи из текста справки."""
    if not text.strip():
        return []
    lines = _bash("help", [], os.path.expanduser("~"), stdin=text, timeout=HELP_TIMEOUT)
    return sorted({l.strip() for l in lines or [] if l.strip().startswith("-")})
```

В `AGENTS.md` в таблицу Layout после строки `shell.py` вставить:

```markdown
| `complete.py` | what a command expects at the next position (program, subcommand, file, directory, options): asks bash-completion like Tab does, options from man or `--help`. Knows nothing about the daemon |
```

- [ ] **Step 4: Убедиться, что проходит**

Run: `~/.local/share/nohands/venv/bin/python -m pytest test_shell.py -k complete -q`
Expected: PASS. Если «после sudo — имя программы» падает — вывести
`C.position(["sudo"], work)[1][:5]` и `len(...)`, сравнить с
`len(C.programs())`: порог «половина» мог не сработать из-за дублей; не
подгонять порог без замера.

Run: `~/.local/share/nohands/venv/bin/python test_shell.py 2>&1 | grep -E "FAIL|ИТОГ"`
Expected: `ИТОГ: ПРОЙДЕН`.

- [ ] **Step 5: Коммит**

```bash
git add complete.py
git commit -m "Консоль: complete.py — что ждёт позиция, у bash-completion

- вид позиции (файл, каталог, слово, программа) и кандидаты — так же,
  как их знает Tab; перехват filedir говорит, где ждут путь
- ключи: completion, затем man, затем --help (только из PATH, таймаут,
  своя группа процессов)
- сбои копятся и уходят в журнал по разу, без кандидатов и путей

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- complete.py test_shell.py AGENTS.md
```

---

### Task 3: `_fit` — каждый токен под свою позицию

**Files:**
- Modify: `shell.py` — `import complete`; удалить `BUILTINS` (`shell.py:169-171`);
  новые `PROGRAM_FIT`, `WORD_FIT`, `SEPARATORS`, `NAME`, `_program`, `_word`,
  `_option`, `_fit_path`, `_fit` (перед `rules`); переписать `rules`
  (`shell.py:581-614`), `runnable` (`shell.py:617-619`), начало `to_command`
  (`shell.py:828-834`)
- Modify: `README.md:180-184` (абзац о подборе)
- Test: `test_shell.py` (`test_fit`, вызов в `main()`)

**Interfaces:**
- Consumes: `complete.programs/position/options/note/drain` (задача 2),
  `shell._closest` (задача 1), `standins()` (задача 2).
- Produces: `shell.rules(raw, cwd) -> str` — прежняя сигнатура;
  `shell.runnable(cmd) -> bool` — по `complete.programs()` и `shutil.which`.

- [ ] **Step 1: Написать падающий тест**

В `test_shell.py` после `test_complete` добавить:

```python
def test_fit():
    print("── подбор по позиции ──")
    d, env = standins()
    work = os.path.join(d, "work")
    with mock.patch.dict(os.environ, env):
        for said, want in [
            ("ривер ктл", "riverctl"),                          # два слова — одно имя
            ("риверпорт", "riverportd"),                        # 0.95, соперников нет
            ("атлас ктл статус", "atlasctl status"),
            ("атлас ктл статуз", "atlasctl status"),            # 0.83
            ("атлас ктл деплой деплой стейджинг", "atlasctl deploy deploy-staging"),
            ("атлас ктл статус ноутс точка тэ икс тэ", "atlasctl status notes.txt"),
            ("атлас ктл два минуса вербоус", "atlasctl --verbose"),   # из completion
            ("атлас ктл минус вербоус", "atlasctl --verbose"),
            ("ривер ктл два минуса фоллоу", "riverctl --follow"),     # из man
            ("харбор ктл два минуса регион", "harborctl --region"),   # из --help
            ("сиди деплой стейджинг", "cd deploy-staging"),           # cd — тоже позиция dirs
            ("эл эс пайп атлас ктл статус", "ls | atlasctl status"),
            ("судо атлас ктл статус", "sudo atlasctl status"),
        ]:
            check(said, S.rules(said, work), want)
        # самое похожее на программу слово намерения — 0.73 (плавать -> pacat)
        for w in ("покажи", "сделай", "плавать", "сколько", "останови", "открой"):
            check(f"«{w}» — не программа", S.rules(w + " файлы", work).split()[0], w)
        check("каталога терминала нет — фраза собрана",
              S.rules("атлас ктл статуз", os.path.join(d, "gone")), "atlasctl статуз")
        check("программа из PATH — runnable", S.runnable("atlasctl status"), True)
        t = time.time()
        S.rules("эхо привет как дела у тебя сегодня утром", work)
        check("семь слов без completion — быстрее 2 с", time.time() - t < 2, True)
        S.complete.drain()
        with mock.patch.object(S.complete, "position", side_effect=RuntimeError("сбой")):
            check("подбор упал — фраза как сказана", S.rules("атлас ктл статуз", work),
                  "атлас ктл статуз")
        check("  и об этом в журнале", any("RuntimeError" in m for m in S.complete.drain()),
              True)
        logged = []
        with mock.patch.object(S.complete, "COMPLETION", os.path.join(d, "missing-fit")):
            S.to_command("атлас ктл статуз", work, None, logged.append)
        check("сбой подбора — в журнал консоли", any("консоль:" in m for m in logged), True)
```

В `main()` после `test_complete()` — строка `    test_fit()`.

- [ ] **Step 2: Убедиться, что падает**

Run: `~/.local/share/nohands/venv/bin/python -m pytest test_shell.py -k fit -q`
Expected: FAIL — первая же проверка: `'ривер ктл'` даёт `'ривер ктл'`,
ждали `'riverctl'`.

- [ ] **Step 3: Реализация**

3a. В `shell.py` после строки `import difflib, getpass, …` добавить:

```python
import complete
```

3b. Удалить `BUILTINS = {…}` (три строки, `shell.py:169-171`). `SHELLS`
оставить — им пользуется `_foreground_shells`.

3c. После `_merge_synonyms` (перед `SAFE = re.compile…`) — константы и
помощники:

```python
# (порог, отрыв) нечёткого выбора; замер 04.10. Программ 3586: нужные —
# docker 0.91, pytest 0.92, kubectl 0.86, systemctl 0.89 с отрывом от 0.15;
# слова намерения не выше 0.73 («плавать» -> pacat), поэтому «покажи файлы»
# не становится программой. Что выбор находит не то («тач» -> tac 0.86 с
# отрывом 0.11) или не находит («хед» — отрыв 0.06), — исключения в WORDS:
# они проверяются раньше.
PROGRAM_FIT = (0.78, 0.10)
# Подкоманды, службы, хосты, ключи — списки в десятки слов (git, systemctl
# --user, docker, apt): нужные 0.80–1.00 с отрывом от 0.15, посторонние
# слова доходят до 0.77 («сервер» -> service) и 0.75
# («ридми» -> rmi). Ниже порога остались клон 0.67, мерж 0.60, ремув 0.73.
WORD_FIT = (0.80, 0.15)
# После них — снова имя программы.
SEPARATORS = {"|", "||", "&&", ";", "&"}
# Слово из одних букв: из таких whisper пишет имя программы и подкоманду.
NAME = re.compile(r"[^\W\d_]+")


def _program(toks, i):
    """Имя программы из 1–3 сказанных слов: (имя, сколько слов взято).
    «пай тест» — pytest. Точное имя из одного слова берётся сразу; иначе
    лучшее по сходству, при равном — из большего числа слов. Не нашлось —
    (как сказано, 1): дальше решает LLM."""
    t = toks[i]
    names = complete.programs()
    if t in names or not NAME.fullmatch(t):
        return t, 1
    found = []
    for n in (1, 2, 3):
        group = toks[i:i + n]
        if len(group) < n or not all(NAME.fullmatch(w) for w in group):
            break
        hit, ratio = _closest("".join(group), names, *PROGRAM_FIT, variants=True)
        if hit and ratio == 1.0 and n == 1:
            return hit, 1
        if hit:
            found.append((ratio, n, hit))
    if not found:
        return t, 1
    ratio, n, hit = max(found)
    return hit, n


def _word(t, cands):
    """Сказанное слово -> кандидат позиции (подкоманда, служба, хост) или None."""
    if t in cands:
        return t
    return _closest(t, cands, *WORD_FIT)[0] if NAME.fullmatch(t) else None


def _option(t, cmd):
    """«--хелп» -> --help: длинный ключ программы, похожий на сказанное.
    Одним минусом тоже: «минус вербоус» — это --verbose, а не -в…"""
    m = re.fullmatch(r"-{1,2}([а-я][а-я-]*)", t)
    if not m or not cmd:
        return t
    longs = {o.rstrip("=")[2:]: o.rstrip("=") for o in complete.options(cmd)
             if o.startswith("--")}
    hit = _closest(m.group(1), list(longs), *WORD_FIT)[0]
    return longs[hit] if hit else t


def _fit_path(toks, i, cwd, file_ok=False):
    """Путь из слов с позиции i: (путь, сколько слов взято). Самый длинный
    хвост, с которым путь есть на диске: «точка слэш сервер билдер» —
    ./server-builder, а не ./сервер и отдельный аргумент; «атлас медиа фьюжн»
    после cd — atlasmedia/fusion. Не нашёлся — слово как имя файла."""
    for j in range(len(toks), i, -1):
        path = _walk(toks[i:j], cwd, file_ok=file_ok)
        if path is not None:
            return path, j - i
    return _resolve(toks[i], cwd), 1


def _fit(toks, cwd):
    """Токены -> слова командной строки, каждое подобрано под свою позицию.

    Что ждёт позиция — имя программы, подкоманду, хост, каталог, файл, —
    говорит bash-completion (complete.position), как Tab в терминале.
    Позиция неизвестна (нет completion, каталога) — аргумент считается
    именем файла, как было до подбора."""
    out, cmd, prog, i = [], [], 0, 0     # cmd — слова текущей команды; prog — где в ней программа
    while i < len(toks):
        t = toks[i]
        if t in SEPARATORS:
            out.append(t)
            cmd, prog, i = [], 0, i + 1
            continue
        # $HOME — переменная, а не имя файла: нечёткий поиск нашёл бы ей
        # похожий каталог. Операторы (>, >>) — как есть.
        if t.startswith("$") or t in SYMBOLS.values() or t == ">>":
            out.append(t)
            cmd.append(t)
            i += 1
            continue
        if "/" in t and not t.startswith("-"):
            got, n = _fit_path(toks, i, cwd, file_ok=True)
        elif t.startswith("-"):
            got, n = _option(t, cmd[prog:]), 1
        else:
            kind, cands = (("program", []) if not cmd
                           else complete.position(cmd, cwd) or ("files", []))
            hit = _word(t, cands) if kind == "words" else None
            if kind == "program":
                prog = len(cmd)
                got, n = _program(toks, i)
            elif kind == "dirs":
                got, n = _fit_path(toks, i, cwd)
            elif hit:
                got, n = hit, 1
            else:
                got, n = _resolve(t, cwd), 1
        out.append(_quote(got, cwd))
        cmd.append(got.replace(JOIN, " "))
        i += n
    return out
```

3d. `rules` целиком заменить на:

```python
def rules(raw, cwd):
    toks = _glue(_lex(_merge_synonyms(_words(raw))))
    # Переменная бывает и первой: «доллар хоум» — продолжение после «эхо»
    toks = [_var(t) if t.startswith("$") else t for t in toks]
    try:
        return " ".join(_fit(toks, cwd))
    except Exception as ex:
        # Подбор — не повод терять фразу: вставится как сказано. В журнал —
        # вид и место, без текста: там бывает имя файла пользователя.
        tb = traceback.extract_tb(ex.__traceback__)[-1]
        complete.note(f"подбор упал: {type(ex).__name__} "
                      f"({os.path.basename(tb.filename)}:{tb.lineno})")
        return " ".join(t.replace(JOIN, " ") for t in toks)
```

В строку импорта `import difflib, getpass, …` добавить `traceback`
(по алфавиту — после `tomllib`).

3e. `runnable` заменить на:

```python
def runnable(cmd):
    first = cmd.split()[0] if cmd.split() else ""
    return bool(first) and (first in complete.programs() or shutil.which(first) is not None)
```

3f. В `to_command` блок от `cmd = rules(raw, cwd)` до `cmd = " ".join(toks)`
включительно заменить на:

```python
    cmd = rules(raw, cwd)
    toks = cmd.split()
    # whisper дописывает спереди слог: «в клауде» — это claude, а не просьба.
    # Без слога фраза разбирается заново: имя программы подбирается только
    # в начале команды.
    if len(toks) > 1 and toks[0].isalpha() and not runnable(toks[0]):
        rest = rules(" ".join(raw.split()[1:]), cwd)
        if runnable(rest):
            cmd, toks = rest, rest.split()
    for msg in complete.drain():
        log(f"консоль: {msg}")
```

3g. В `README.md` после абзаца «Имена файлов сверяются с каталогом
терминала…» (заканчивается «…придётся поправить руками.») вставить абзац:

```markdown
Программы, подкоманды и ключи берутся с машины, а не из списка: что
установлено и что на этом месте предложил бы Tab (bash-completion). «гит
статус» находит `status` среди подкоманд git, «систем ктл рестарт» —
`restart` у systemctl; где Tab ждёт каталог (после `cd`), имя собирается из
нескольких слов. Ключ словами — «два минуса хелп» -> `--help` — ищется в
completion, затем в man, затем в `--help` программы.
```

- [ ] **Step 4: Убедиться, что проходит**

Run: `~/.local/share/nohands/venv/bin/python -m pytest test_shell.py -k fit -q`
Expected: PASS.

Run: `~/.local/share/nohands/venv/bin/python test_shell.py 2>&1 | grep -E "FAIL|ИТОГ"`
Expected: `ИТОГ: ПРОЙДЕН`. Старые случаи `cd` (`test_paths`) теперь идут
через позицию `dirs` у настоящего completion `cd` — если какой-то из них
упал, сравнить `S.complete.position(["cd"], каталог_теста)` с ожиданием
`dirs` прежде, чем менять `_fit`.

- [ ] **Step 5: Коммит**

```bash
git commit -m "Консоль: каждый токен подбирается под свою позицию

- имя программы — из установленных, нечётко, из 1–3 слов; позиция имени
  после sudo и подобных узнаётся по кандидатам completion
- подкоманды, службы, хосты — из кандидатов позиции; где ждут каталог,
  имя собирается из нескольких слов, как раньше только у cd
- ключ словами («два минуса хелп») — из completion, man или --help
- runnable — по compgen -c вместо списка BUILTINS
- упавший подбор не теряет фразу: вставится как сказано, в журнал — место

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- shell.py test_shell.py README.md
```

---

### Task 4: `WORDS` — только исключения

**Files:**
- Modify: `shell.py:78-142` (`WORDS` и комментарий над ним)
- Разовый скрипт: `$SCRATCH/prune_words.py`, где `SCRATCH` — scratchpad
  сеанса (в репозиторий не кладётся)

**Interfaces:**
- Consumes: `shell.rules`, `shell.WORDS`, `shell.TABLE`, `shell.LETTERS`,
  `shell.SYMBOLS` (задачи 1–3).
- Produces: сокращённый `WORDS`; список удалённых — в сообщении коммита.

- [ ] **Step 1: Разовая проверка — какие записи подбор находит сам**

Записать в `$SCRATCH/prune_words.py`:

```python
"""Какие записи WORDS подбор находит сам. Разовая проверка, не в репозитории."""
import sys, tempfile
sys.path.insert(0, "/путь/к/nohands")
import shell as S

cwd = tempfile.mkdtemp()        # пустой каталог: не настоящие каталоги пользователя
# Где запись работает: сама по себе (программа) или после программы
# (подкоманда, ключ). Программы — латиницей: их записи тоже проверяются.
PARENTS = ["", "git ", "docker ", "docker compose ", "systemctl --user ", "apt ",
           "kubectl ", "terraform ", "ls два минуса ", "ssh "]


def without(key):
    """Убрать запись из таблицы разбора; вернуть, как её восстановить."""
    S.WORDS.pop(key)
    old = S.TABLE.pop(key)
    if key in S.LETTERS:
        S.TABLE[key] = ("letter", S.LETTERS[key])
    elif key in S.SYMBOLS:
        S.TABLE[key] = ("sym", S.SYMBOLS[key])
    return old


removable, kept = [], []
for key, val in list(S.WORDS.items()):
    before = {p: S.rules(p + key, cwd) for p in PARENTS}
    useful = [p for p, got in before.items() if got.endswith(val)]
    old = without(key)
    same = [p for p in useful if S.rules(p + key, cwd) == before[p]]
    S.WORDS[key], S.TABLE[key] = val, old
    (removable if same else kept).append((key, val, same or useful))
print("── удалить ──")
for key, val, where in removable:
    print(f"{key!r:<22} {val!r:<14} {where}")
print("── оставить ──")
for key, val, where in kept:
    print(f"{key!r:<22} {val!r:<14} {where}")
```

Run: `~/.local/share/nohands/venv/bin/python $SCRATCH/prune_words.py`
Expected: два списка. В «оставить» должны оказаться как минимум «тач»,
«хед», «тейл», «питон», «код»/«ходе», «клод»/«клауде»…, «эс си пи»,
«рут». Если кто-то из них попал в «удалить» — запись не удалять и
разобраться, на какой фразе совпало.

- [ ] **Step 2: Удалить записи из «удалить»**

Удалить из `WORDS` каждую запись списка «удалить». Комментарии групп
(`# файлы и каталоги`, `# сеть`…), у которых не осталось записей, удалить
вместе с ними. Комментарий над `WORDS` заменить на:

```python
# Исключения: как произносят -> как пишут — только то, до чего подбор по
# установленным программам и completion (_fit) не дотягивается или находит
# не то. Транслит по буквам не знает английского произношения: «тач» ->
# tach ближе к tac (0.86), чем к touch; «хед» — head с отрывом 0.06 от hd;
# «питон», «клод», «ходе» — 0.67–0.75 с чужими программами. И буквы,
# которые перехватило бы слово покороче («эс си пи» без записи дало бы
# «s cp»). Остальное подбирается само (замер 04.10).
```

- [ ] **Step 3: Проверить, что ничего не сломалось**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py 2>&1 | grep -E "FAIL|ИТОГ"`
Expected: `ИТОГ: ПРОЙДЕН`. Если упал случай с удалённым словом — вернуть
эту запись в `WORDS` (подбор на ней не равен прежнему в контексте теста) и
перезапустить.

Run: `~/.local/share/nohands/venv/bin/python $SCRATCH/prune_words.py | sed -n '/удалить/,/оставить/p'`
Expected: под «удалить» пусто — всё, что подбор находит сам, уже удалено.

- [ ] **Step 4: Коммит**

В тело вписать список удалённых записей из шага 1 (ключи через запятую).

```bash
git commit -m "Консоль: в WORDS только исключения

Программы, подкоманды и ключи теперь подбираются с машины; записи, без
которых результат тот же, удалены (проверка каждой — фраза с ней и без
неё после программы: git, docker, systemctl, apt, kubectl, terraform):
<список удалённых ключей>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- shell.py
```

- [ ] **Step 5: Применить к демону**

Run: `nohands restart && sleep 3 && nohands log | tail -5`
Expected: служба поднялась, в журнале нет `Traceback`. Живую проверку
голосом делает пользователь.
