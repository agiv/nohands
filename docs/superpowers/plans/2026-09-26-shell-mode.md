# Режим КОНСОЛЬ — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Голосовой режим «консоль»: сказанная фраза становится командой шелла и вставляется в терминал без Enter. Запускает её фраза «ввод».

**Architecture:** Новый модуль `shell.py` без зависимостей от демона: правила (символы, буквы, программы, пути по настоящей ФС) → проверка «первое слово — программа?» → иначе LLM (ollama). В `voiced.py` новое состояние `SHELL`, устроенное как ДИКТОВКА: vosk слушает только выход и «ввод», whisper копит речь, а результат проходит через `shell.to_command` и вставляется Ctrl+Shift+V.

**Tech Stack:** Python 3.14 (stdlib: `difflib`, `tomllib`, `urllib`, `shlex`), vosk, faster-whisper, ollama HTTP API, evdev/uinput.

**Spec:** `docs/superpowers/specs/2026-09-26-shell-mode-design.md`

## Global Constraints

- Никаких новых pip-зависимостей: HTTP к ollama — через `urllib`, конфиг — через `tomllib`.
- Фразы режима — только названные пользователем: вход «консоль», выход «хватит консоль» (и «конец текста»), запуск «ввод».
- Команда вставляется **без Enter**; Enter — только по «ввод».
- Конфиг: `~/.config/nohands/config.toml`, секция `[shell.llm]`, ключи `backend` (`"ollama"` | `"none"`), `model` (по умолчанию `"qwen2.5-coder:3b"`), `url` (по умолчанию `"http://localhost:11434"`). Нет файла — значения по умолчанию.
- Таймаут LLM — 10 с; любая её ошибка — вставить результат правил и записать предупреждение в журнал.
- Тесты — обычные скрипты с `main()` и кодом выхода, как `test_commands.py`, запуск `~/.local/share/nohands/venv/bin/python <файл>`; pytest в проекте нет.
- Комментарии и сообщения журнала — по-русски, в стиле соседнего кода; коммиты — по-русски, короткой фразой, как в `git log`.
- Каждый коммит заканчивается строкой `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **«ввод» внутри надиктованной фразы** («эхо ввод данных») не должен жать Enter: иначе недоговорённая команда выполнится. Ожидается: Enter только от отдельной фразы «ввод» с уверенностью ≥ `MIN_CONF`. Тест — Task 3, шаг 1.
2. **Короткие команды** («топ», «пи дабл ю ди» ~0,5 с) не должны отбрасываться порогом 0,8 с, который в диктовке отсекает шум. Ожидается: в КОНСОЛИ порог `MIN_SHELL_S = 0.5`. Проверка — Task 3, шаг 7 (вручную, голосом).
3. **ollama не запущена или отвечает медленно** — фраза намерением должна вставить хоть что-то, а демон не должен упасть. Ожидается: результат правил и строка в журнале. Тест — Task 2, «LLM упала».
4. **Ответ LLM в markdown или с `$ `** не должен попадать в терминал с ```` ``` ```` и приглашением. Тест — Task 2, `clean_reply`.
5. **Имена с пробелами** (`Рабочий стол`) должны вставляться в кавычках, иначе `cd` получит два аргумента. Тест — Task 1, «рабочий стол по-русски».

---

## Файлы

- Create: `shell.py` — фраза → команда: правила, LLM, cwd терминала.
- Create: `test_shell.py` — проверка `shell.py` без звука и сети.
- Modify: `voiced.py` — состояние `SHELL`, грамматика, Ears, вставка, обработка фраз.
- Modify: `tray.py` — пункт «Консоль» и значок.
- Modify: `test_commands.py` — фразы КОНСОЛИ на синтезированной речи.
- Modify: `bootstrap.sh` — ollama и модель.
- Modify: `README.md` — режим, раздел «Консоль», число фраз.
- Modify: `deploy/nohands` — фразы режима в `nohands команды`.

---

### Task 1: Правила — фраза в команду

**Files:**
- Create: `shell.py`
- Test: `test_shell.py`

**Interfaces:**
- Produces: `shell.rules(raw: str, cwd: str) -> str`, `shell.runnable(cmd: str) -> bool`, `shell.WHISPER_PROMPT: str`, `shell.HOME: str`, `shell.SHELLS: set[str]`.

- [ ] **Step 1: Написать тест**

`test_shell.py`:

```python
#!/usr/bin/env python3
"""Проверка режима КОНСОЛЬ без микрофона, GPU и сети.

Запуск: ~/.local/share/nohands/venv/bin/python test_shell.py

Правила сверяются с временным каталогом, где заранее известно, что лежит,
а LLM подменяется заглушкой: проверяется не её ум, а то, когда её зовут и
что бывает, если она молчит.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shell as S

FAILS = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<46} -> {got!r}"
          + ("" if ok else f" (ждали {want!r})"))


def sandbox(*dirs, files=()):
    d = tempfile.mkdtemp(prefix="nohands-shell-")
    for p in dirs:
        os.makedirs(os.path.join(d, p))
    for p in files:
        open(os.path.join(d, p), "w").close()
    return d


def test_rules(d):
    print("── правила ──")
    for said, want in [
        ("эл эс минус эл эй", "ls -la"),
        ("гит статус", "git status"),
        ("ди эф минус эйч", "df -h"),
        ("эл эс два минуса хелп", "ls --help"),
        ("сиди точка точка", "cd .."),
        ("гит эд пробел точка", "git add ."),
        ("гит коммит минус эм", "git commit -m"),
        ("эл эс звездочка точка тэ икс тэ", "ls *.txt"),
        ("ls -la.", "ls -la"),                     # whisper написал латиницей
        ("Сиди, загрузки.", "cd Downloads"),       # и расставил знаки
    ]:
        check(said, S.rules(said, d), want)


def test_paths(d):
    print("── пути ──")
    for said, want in [
        ("сиди загрузки", "cd Downloads"),
        ("сиди рабочий стол", "cd Desktop"),       # синоним XDG
        ("кэт ноутс точка тэ икс тэ", "cat notes.txt"),
        ("сиди проджектс слэш нохэндс", "cd Projects/nohands"),
        ("кэт ноутс точка тэ икс тэ пайп греп тодо", "cat notes.txt | grep тодо"),
        ("эхо привет больше больше ноутс точка тэ икс тэ", "echo привет >> notes.txt"),
        ("сиди тильда", "cd ~"),
        ("сиди слэш ю эс эр", "cd /usr"),
        ("сиди несуществующий", "cd несуществующий"),
    ]:
        check(said, S.rules(said, d), want)
    # каталог назван по-русски — синоним не нужен, находит нечёткое сравнение
    check("рабочий стол по-русски", S.rules("сиди рабочий стол", sandbox("Рабочий стол")),
          "cd 'Рабочий стол'")
    # не нашлось ничего — слова остаются как сказаны, пробел не теряется
    check("рабочий стол в пустом каталоге", S.rules("сиди рабочий стол", sandbox()),
          "cd 'рабочий стол'")


def test_runnable():
    print("── программа ли это ──")
    for cmd, want in [("ls -la", True), ("cd ..", True), ("покажи файлы", False), ("", False)]:
        check(repr(cmd), S.runnable(cmd), want)


def main():
    d = sandbox("Downloads", "Desktop", "Projects/nohands", files=["notes.txt"])
    test_rules(d)
    test_paths(d)
    test_runnable()
    print("\nИТОГ:", "ПРОЙДЕН" if not FAILS else f"ПРОВАЛЕН: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py`
Expected: `ModuleNotFoundError: No module named 'shell'`

- [ ] **Step 3: Написать `shell.py`**

```python
"""Режим КОНСОЛЬ: сказанная фраза -> команда шелла.

Сначала правила: произносимые символы, буквы флагов, имена программ и пути,
сверенные с настоящей файловой системой. Если первое слово результата — не
программа и не встроенная команда оболочки, фраза, скорее всего, сказана
намерением («покажи файлы в загрузках»), и её переводит LLM.

Модуль не знает о демоне, микрофоне и whisper: на входе текст и каталог,
на выходе строка команды. Поэтому он проверяется без звука и без сети.
"""

import difflib, os, re, shlex, shutil

HOME = os.path.expanduser("~")

# Подсказка whisper: слова из неё он охотнее пишет так же, кириллицей, —
# а правила ждут именно кириллицу.
WHISPER_PROMPT = ("гит статус, сиди тильда слэш загрузки, эл эс минус эл эй, "
                  "кэт точка тэ икс тэ, судо эпт, два минуса хелп, пайп греп")

# Ключи нормализованы как в norm(): нижний регистр, «ё» -> «е».
SYMBOLS = {
    "два минуса": "--", "двойной минус": "--",
    "слэш": "/", "слеш": "/", "точка": ".", "тильда": "~",
    "минус": "-", "дефис": "-", "подчеркивание": "_", "равно": "=",
    "двоеточие": ":", "доллар": "$", "кавычка": '"',
    "пайп": "|", "труба": "|", "больше": ">", "меньше": "<",
    "амперсанд": "&", "звездочка": "*",
}
# Как символ стыкуется с соседями: BOTH — с обеими сторонами (пути, имена
# файлов), RIGHT — только со следующим (флаги, переменные), остальные —
# операторы, они стоят отдельно.
GLUE_BOTH = {"/", ".", "_", "=", ":", "*"}
GLUE_RIGHT = {"-", "--", "~", "$", '"'}

LETTERS = {
    "эй": "a", "би": "b", "си": "c", "ди": "d", "эф": "f", "джи": "g",
    "эйч": "h", "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эм": "m",
    "эн": "n", "оу": "o", "пи": "p", "кью": "q", "эр": "r", "эс": "s",
    "ти": "t", "тэ": "t", "ю": "u", "ви": "v", "дабл ю": "w", "икс": "x",
    "уай": "y", "зед": "z",
}
# Программы и частые подкоманды: как их произносят -> как пишут.
WORDS = {
    "гит": "git", "эл эс": "ls", "элэс": "ls", "сиди": "cd", "си ди": "cd",
    "кэт": "cat", "кет": "cat", "судо": "sudo", "греп": "grep", "вим": "vim",
    "нано": "nano", "питон": "python3", "эм кей дир": "mkdir", "мкдир": "mkdir",
    "эр эм": "rm", "эм ви": "mv", "си пи": "cp", "тач": "touch", "эхо": "echo",
    "пи дабл ю ди": "pwd", "лесс": "less", "хед": "head", "тейл": "tail",
    "эпт": "apt", "ман": "man", "пинг": "ping", "кил": "kill", "топ": "top",
    "эйч топ": "htop", "докер": "docker", "найти": "find", "файнд": "find",
    "статус": "status", "эд": "add", "коммит": "commit", "пуш": "push",
    "пул": "pull", "лог": "log", "дифф": "diff", "чекаут": "checkout",
    "клон": "clone", "хелп": "help", "инсталл": "install",
}
SPACE = "пробел"        # принудительный разрыв: «гит эд пробел точка» -> git add .
TABLE = {**{k: ("sym", v) for k, v in SYMBOLS.items()},
         **{k: ("letter", v) for k, v in LETTERS.items()},
         **{k: ("word", v) for k, v in WORDS.items()},
         SPACE: ("space", "")}
LONGEST = max(len(k.split()) for k in TABLE)

# Каталоги XDG по-русски: транслитерация до них не дотягивается.
SYNONYMS = {
    "загрузки": "Downloads", "документы": "Documents", "рабочий стол": "Desktop",
    "музыка": "Music", "изображения": "Pictures", "картинки": "Pictures",
    "видео": "Videos", "общедоступные": "Public", "шаблоны": "Templates",
}
SYNONYM_LONGEST = max(len(k.split()) for k in SYNONYMS)
# Склейка многословного синонима в одно слово до разбора. Неразрывный пробел,
# а не «_»: подчёркивание бывает в настоящих именах файлов.
JOIN = "\u00a0"

BUILTINS = {"cd", "export", "source", ".", "alias", "unalias", "echo", "pwd",
            "exit", "history", "set", "unset", "type", "jobs", "fg", "bg",
            "exec", "eval", "read", "wait", "umask", "ulimit", "pushd", "popd"}
SHELLS = {"bash", "zsh", "fish", "sh", "dash"}

TRANSLIT = [("дж", "j"), ("кс", "x"), ("а", "a"), ("б", "b"), ("в", "v"),
            ("г", "g"), ("д", "d"), ("е", "e"), ("ж", "zh"), ("з", "z"),
            ("и", "i"), ("й", "y"), ("к", "k"), ("л", "l"), ("м", "m"),
            ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"), ("с", "s"),
            ("т", "t"), ("у", "u"), ("ф", "f"), ("х", "h"), ("ц", "ts"),
            ("ч", "ch"), ("ш", "sh"), ("щ", "sch"), ("ъ", ""), ("ы", "y"),
            ("ь", ""), ("э", "e"), ("ю", "yu"), ("я", "ya")]


def translit(s):
    for ru, en in TRANSLIT:
        s = s.replace(ru, en)
    return s


def norm(text):
    """Текст whisper -> слова. Знаки, которые whisper пишет сам, сохраняем."""
    s = text.lower().replace("ё", "е").strip()
    s = re.sub(r"[,!?…«»]", " ", s)
    if s.endswith(".") and not s.endswith(".."):
        s = s[:-1]          # точка в конце предложения, а не в команде
    return s.split()


def _lex(words):
    """Слова -> (вид, текст) по самому длинному совпадению с таблицей."""
    out, i = [], 0
    while i < len(words):
        for n in range(min(LONGEST, len(words) - i), 0, -1):
            key = " ".join(words[i:i + n])
            if key in TABLE:
                out.append(TABLE[key])
                i += n
                break
        else:
            w = words[i]
            out.append(("sym", w) if w in SYMBOLS.values() else ("word", w))
            i += 1
    return out


def _glue(items):
    """(вид, текст) -> токены командной строки."""
    toks, pending, last = [], False, None
    for kind, text in items:
        if kind == "space":
            pending, last = False, "space"
            continue
        if kind == "sym" and text in GLUE_BOTH:
            # к имени программы не липнем: «сиди точка точка» — это cd ..
            join = bool(toks) and len(toks) > 1 and last not in ("op", "space")
            join = join or pending
            pending, kind = True, "path"
        elif kind == "sym" and text in GLUE_RIGHT:
            join = pending and last == "path"
            pending = True
        elif kind == "sym":         # оператор; >> и && собираем из двух
            join = bool(toks) and toks[-1] == text and last == "op"
            pending, kind = False, "op"
        elif kind == "letter":
            join = pending or last == "letter"
            pending = False
        else:
            join, pending = pending, False
        if join and toks:
            toks[-1] += text
        else:
            toks.append(text)
        last = kind
    return toks


def _resolve(arg, cwd):
    """Аргумент-путь -> настоящие имена из файловой системы, где нашлись."""
    if arg.startswith("~"):
        base, parts, prefix = HOME, arg[1:].lstrip("/").split("/"), "~"
    elif arg.startswith("/"):
        base, parts, prefix = "/", arg[1:].split("/"), ""
    else:
        base, parts, prefix = cwd, arg.split("/"), None
    done = []
    for k, part in enumerate(parts):
        if part in ("", ".", "..") or os.path.exists(os.path.join(base, part)):
            done.append(part)
            base = os.path.join(base, part)
            continue
        hit = _match(part, base)
        if hit is None:
            done.extend(parts[k:])
            break
        done.append(hit)
        base = os.path.join(base, hit)
    path = "/".join(done)
    if prefix is not None:
        path = prefix + "/" + path if path else prefix
    return path


def _match(part, base):
    try:
        names = os.listdir(base)
    except OSError:
        return None
    syn = SYNONYMS.get(part.replace(JOIN, " "))
    if syn in names:
        return syn
    lower = {n.lower(): n for n in names}
    for probe in (part, translit(part)):
        got = difflib.get_close_matches(probe, list(lower), n=1, cutoff=0.7)
        if got:
            return lower[got[0]]
    return None


def _merge_synonyms(words):
    """«рабочий стол» — два слова, но один каталог: склеиваем до разбора."""
    out, i = [], 0
    while i < len(words):
        for n in range(min(SYNONYM_LONGEST, len(words) - i), 1, -1):
            key = " ".join(words[i:i + n])
            if key in SYNONYMS:
                out.append(key.replace(" ", JOIN))
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def rules(raw, cwd):
    toks = _glue(_lex(_merge_synonyms(norm(raw))))
    for k in range(1, len(toks)):
        t = toks[k]
        if t.startswith("-") or t in SYMBOLS.values() or t in ("&&", ">>"):
            continue
        toks[k] = _resolve(t, cwd)
    toks = [t.replace(JOIN, " ") for t in toks]
    return " ".join(shlex.quote(t) if " " in t else t for t in toks)


def runnable(cmd):
    first = cmd.split()[0] if cmd.split() else ""
    return first in BUILTINS or shutil.which(first) is not None
```

- [ ] **Step 4: Запустить тест**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py`
Expected: все строки `ok`, последняя — `ИТОГ: ПРОЙДЕН`, код выхода 0.

- [ ] **Step 5: Коммит**

```bash
git add shell.py test_shell.py
git commit -m "Консоль: правила перевода фразы в команду шелла

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: LLM, настройка и каталог терминала

**Files:**
- Modify: `shell.py` (строка импорта, константы после `HOME`, конец файла)
- Test: `test_shell.py`

**Interfaces:**
- Consumes: `rules`, `runnable`, `HOME`, `SHELLS` из Task 1.
- Produces: `shell.to_command(raw: str, cwd: str, llm, log=print) -> str`; `shell.load_llm(log=print, path=CONFIG) -> Ollama | None`; `shell.terminal_cwd() -> str`; `shell.clean_reply(text: str) -> str`; класс `Ollama(model, url, timeout=10.0)` с методом `complete(prompt: str) -> str` — у любого бэкенда LLM должен быть этот метод.

- [ ] **Step 1: Дописать тесты**

В `test_shell.py` перед `def main():` вставить:

```python
class Stub:
    """LLM-заглушка: запоминает вопросы, отвечает заданным текстом или падает."""
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.asked = reply, error, []

    def complete(self, prompt):
        self.asked.append(prompt)
        if self.error:
            raise self.error
        return self.reply


def test_llm(d):
    print("── запасной путь через LLM ──")
    quiet = lambda *a: None
    llm = Stub("```bash\nls ~/Downloads\n```")
    check("программа — LLM не зовут", S.to_command("гит статус", d, llm, quiet), "git status")
    check("  и вопросов не было", len(llm.asked), 0)
    check("намерение — ответ LLM без markdown",
          S.to_command("покажи файлы в загрузках", d, llm, quiet), "ls ~/Downloads")
    check("  в вопросе есть каталог и файлы",
          d in llm.asked[0] and "notes.txt" in llm.asked[0], True)
    check("LLM упала — результат правил",
          S.to_command("покажи файлы", d, Stub(error=OSError("нет связи")), quiet),
          "покажи файлы")
    check("LLM выключена — результат правил", S.to_command("покажи файлы", d, None, quiet),
          "покажи файлы")
    check("LLM ответила пусто — результат правил",
          S.to_command("покажи файлы", d, Stub(""), quiet), "покажи файлы")
    check("ответ «$ cmd» без приглашения", S.clean_reply("$ ls -l\n"), "ls -l")
    check("ответ в обратных кавычках", S.clean_reply("`pwd`"), "pwd")


def test_config():
    print("── настройка ──")
    quiet = lambda *a: None
    def cfg(text):
        p = os.path.join(tempfile.mkdtemp(), "config.toml")
        with open(p, "w") as f:
            f.write(text)
        return p
    llm = S.load_llm(quiet, path="/нет/такого/файла.toml")
    check("файла нет — ollama по умолчанию",
          (type(llm).__name__, llm.model, llm.url),
          ("Ollama", "qwen2.5-coder:3b", "http://localhost:11434"))
    llm = S.load_llm(quiet, cfg('[shell.llm]\nmodel = "qwen2.5-coder:7b"\n'))
    check("своя модель", llm.model, "qwen2.5-coder:7b")
    check("backend = none", S.load_llm(quiet, cfg('[shell.llm]\nbackend = "none"\n')), None)
    check("неизвестный backend — выключена",
          S.load_llm(quiet, cfg('[shell.llm]\nbackend = "gpt"\n')), None)
    check("битый toml — по умолчанию",
          S.load_llm(quiet, cfg("[shell.llm\n")).model, "qwen2.5-coder:3b")


def test_cwd():
    print("── каталог терминала ──")
    check("существующий каталог", os.path.isdir(S.terminal_cwd()), True)
```

В `main()` после `test_runnable()` добавить:

```python
    test_llm(d)
    test_config()
    test_cwd()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py`
Expected: `AttributeError: module 'shell' has no attribute 'to_command'`

- [ ] **Step 3: Дописать `shell.py`**

Строку импорта заменить на:

```python
import difflib, glob, json, os, re, shlex, shutil, tomllib, urllib.request
```

После `HOME = os.path.expanduser("~")` добавить:

```python
CONFIG = os.path.join(HOME, ".config", "nohands", "config.toml")
DEFAULTS = {"backend": "ollama", "model": "qwen2.5-coder:3b",
            "url": "http://localhost:11434"}
```

В конец файла добавить:

```python
# ─────────────────────────────── LLM ─────────────────────────────────────────
class Ollama:
    def __init__(self, model, url, timeout=10.0):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def complete(self, prompt):
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False,
                           "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(self.url + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["response"]


def load_llm(log=print, path=CONFIG):
    """LLM из ~/.config/nohands/config.toml; None — если выключена."""
    cfg = dict(DEFAULTS)
    try:
        with open(path, "rb") as f:
            cfg.update(tomllib.load(f).get("shell", {}).get("llm", {}))
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as ex:
        log(f"консоль: {path} не прочитан ({ex}), беру настройки по умолчанию")
    if cfg["backend"] == "none":
        return None
    if cfg["backend"] != "ollama":
        log(f"консоль: неизвестный backend {cfg['backend']!r}, LLM выключена")
        return None
    return Ollama(cfg["model"], cfg["url"])


def prompt_for(raw, cwd):
    try:
        names = sorted(os.listdir(cwd))[:50]
    except OSError:
        names = []
    return ("Переведи просьбу пользователя в одну команду bash. Ответь только "
            "командой, одной строкой, без пояснений и без markdown.\n"
            f"Текущий каталог: {cwd}\n"
            f"Файлы в нём: {' '.join(names)}\n"
            f"Просьба (распознана с голоса, возможны ошибки): {raw}\n")


def clean_reply(text):
    lines = [l.strip() for l in text.strip().splitlines()
             if l.strip() and not l.strip().startswith("```")]
    line = lines[0] if lines else ""
    line = line.strip("`").strip()
    return line[2:] if line.startswith("$ ") else line


def to_command(raw, cwd, llm, log=print):
    cmd = rules(raw, cwd)
    if runnable(cmd) or llm is None or not raw.strip():
        return cmd
    try:
        got = clean_reply(llm.complete(prompt_for(raw, cwd)))
    except Exception as ex:
        log(f"консоль: LLM не ответила ({ex}), вставляю по правилам")
        return cmd
    log(f"консоль: LLM {raw!r} -> {got!r}")
    return got or cmd


# ──────────────────────────── cwd терминала ─────────────────────────────────
def _tty_dev(tty_nr):
    major = (tty_nr >> 8) & 0xfff
    minor = (tty_nr & 0xff) | ((tty_nr >> 12) & 0xfff00)
    return major, minor


def terminal_cwd():
    """Каталог оболочки в терминале, куда писали последним.

    Wayland не говорит, какое окно в фокусе, но вставка в терминал меняет
    mtime его /dev/pts/N — самый свежий pts и есть активный терминал.
    """
    try:
        uid = os.getuid()
        pts = [p for p in glob.glob("/dev/pts/[0-9]*") if os.stat(p).st_uid == uid]
        if not pts:
            return HOME
        rdev = os.stat(max(pts, key=lambda p: os.stat(p).st_mtime)).st_rdev
        want = (os.major(rdev), os.minor(rdev))
        best = None
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                with open(f"/proc/{pid}/stat") as f:
                    st = f.read()
                comm = st[st.index("(") + 1:st.rindex(")")]
                fields = st[st.rindex(")") + 2:].split()
                if comm not in SHELLS or _tty_dev(int(fields[4])) != want:
                    continue
                cwd = os.readlink(f"/proc/{pid}/cwd")
                if best is None or fields[2] == fields[5]:   # лидер переднего плана
                    best = cwd
            except (OSError, ValueError, IndexError):
                continue
        return best or HOME
    except OSError:
        return HOME
```

- [ ] **Step 4: Запустить тесты**

Run: `~/.local/share/nohands/venv/bin/python test_shell.py`
Expected: `ИТОГ: ПРОЙДЕН`.

- [ ] **Step 5: Коммит**

```bash
git add shell.py test_shell.py
git commit -m "Консоль: LLM через ollama, config.toml и каталог терминала

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Состояние КОНСОЛЬ в демоне и трее

**Files:**
- Modify: `voiced.py`, `tray.py`
- Test: `test_commands.py`

**Interfaces:**
- Consumes: `shell.to_command`, `shell.load_llm`, `shell.terminal_cwd`, `shell.WHISPER_PROMPT`.
- Produces: `voiced.SHELL == "КОНСОЛЬ"`, `voiced.NORM_SHELL_EXIT: list[str]`, `voiced.NORM_RUN: str`, `grammar_for(SHELL)`; меню трея шлёт `("КОНСОЛЬ", None)`.

- [ ] **Step 1: Дописать проверку в `test_commands.py`**

В списке `checks` (блок «выход из диктовки и пробуждение») после строки с `"хватит печатать"` добавить:

```python
        (V.SHELL, "хватит консоль", V.NORM_SHELL_EXIT, say("хватит консоль")),
        (V.SHELL, "хватит консоль в конце фразы", V.NORM_SHELL_EXIT,
         say("гит статус хватит консоль")),
        (V.SHELL, "ввод", [V.NORM_RUN], say("ввод")),
```

После цикла `for state, name, wanted, pcm in checks:` (перед `print(f"\nложных срабатываний...`) добавить:

```python
    # «ввод» жмёт Enter — внутри фразы он выполнил бы недоговорённую команду.
    # В отличие от выхода, хвостом за речью он не принимается.
    print("\n── «ввод» внутри фразы не запускает команду ──")
    for talk in ("эхо ввод данных", "надо сделать ввод"):
        ears = V.Ears(model, V.SHELL)
        pcm = say(talk)
        got = [g[0] for i in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes()))]
        ok = V.NORM_RUN not in got
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} «{talk}» -> {got}")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `~/.local/share/nohands/venv/bin/python test_commands.py`
Expected: `AttributeError: module 'voiced' has no attribute 'SHELL'`

- [ ] **Step 3: Константы и грамматика в `voiced.py`**

Docstring модуля: строку `Три состояния: СОН -> КОМАНДЫ <-> ДИКТОВКА` заменить на

```
Четыре состояния: СОН -> КОМАНДЫ <-> ДИКТОВКА
                                <-> КОНСОЛЬ (команды шелла, см. shell.py)
```

Импорты: после `from tray import Tray` добавить `import shell`.

```python
SLEEP, COMMAND, DICTATION, SHELL = "СОН", "КОМАНДЫ", "ДИКТОВКА", "КОНСОЛЬ"
```

В `COMMANDS` после `"английский": ("dictate", "en"),` добавить:

```python
    "консоль":         ("shell", None),
```

После `EXIT_DICTATION = [...]`:

```python
SHELL_EXIT = ["хватит консоль", "конец текста"]
SHELL_RUN = "ввод"      # Enter: вставленная команда выполняется только по нему
```

После `NORM_EXIT = [...]`:

```python
NORM_SHELL_EXIT = [norm(x) for x in SHELL_EXIT]
NORM_RUN = norm(SHELL_RUN)
```

Регулярку хвоста обобщить — строки `EXIT_WORDS = ...` и `EXIT_TAIL_RE = ...` заменить на:

```python
def tail_re(phrases):
    words = sorted({w for p in phrases for w in p.split()}, key=len, reverse=True)
    return re.compile(r"[\s,.!?…-]*\b(?:" + "|".join(words) + r")\b[\s,.!?…-]*$", re.I)

EXIT_TAIL_RE = tail_re(NORM_EXIT)
SHELL_TAIL_RE = tail_re(NORM_SHELL_EXIT + [NORM_RUN])
```

В `feedback` в словарь звуков добавить `SHELL: "dialog-information"`.

В `grammar_for` перед `else:`:

```python
    elif state == SHELL:
        phrases = NORM_SHELL_EXIT + [NORM_RUN]
```

- [ ] **Step 4: Ears — без барьеров, но «ввод» только отдельной фразой**

В `Ears.listen` строки `self.guard = ...` и `self._tails = ...` заменить на:

```python
        self.guard = state not in (SLEEP, DICTATION, SHELL)
        # Фразы режима, которые могут идти хвостом за посторонней речью.
        # «Ввод» — не может: он жмёт Enter, и «эхо ввод данных» выполнило бы
        # недоговорённую команду. Без хвоста грамматика отдаёт такое как
        # «unk ввод», а это не фраза режима — handle_text её пропустит.
        self._tails = [p for p in json.loads(grammar_for(state))
                       if p not in ("[unk]", NORM_RUN)]
```

В комментарии над ним «там 63 фразы» заменить на «там 64 фразы».

- [ ] **Step 5: Вставка, выполнение, накопление речи**

`Paster.paste` — сочетание вставки параметром:

```python
    def paste(self, text, keys=(E.KEY_LEFTCTRL, E.KEY_V)):
```

и внутри `self.inp.tap(E.KEY_LEFTCTRL, E.KEY_V)` → `self.inp.tap(*keys)`. После класса `Paster`:

```python
# Терминалы GNOME (Ptyxis, gnome-terminal) Ctrl+V не вставляют: это сочетание
# уходит в программу как символ ^V.
TERMINAL_PASTE = (E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_V)
```

`Daemon`: рядом с `MIN_SPEECH_S` добавить

```python
    MIN_SHELL_S = 0.5      # «топ» и «сиди» короче порога диктовки в 0.8 с
```

В `__init__` после `self.paster = Paster(self.inp)`: `self.llm = shell.load_llm(log)`.

`_check_vocab`: `phrases = list(NORM_COMMANDS) + list(NORM_WAKE) + NORM_EXIT + NORM_SHELL_EXIT`.

`run_command`, после ветки `"dictate"`:

```python
        elif kind == "shell":
            self._ensure_whisper()
            self.set_state(SHELL)
```

`apply_request`: сразу после блока `if state == "ГОЛОВА": ... return` добавить

```python
        if self.state == SHELL and state != SHELL:
            self.flush_dictation()          # сказанное до щелчка не пропадёт
```

а финальный `if state == DICTATION: ... else: ...` заменить на

```python
        if state == DICTATION:
            self.run_command(("dictate", lang))
        elif state == SHELL:
            self.run_command(("shell", None))
        else:
            self.set_state(state)
```

`flush_dictation`: в начале после docstring `in_shell = self.state == SHELL`; строку `if secs < 0.8:` → `if secs < (self.MIN_SHELL_S if in_shell else 0.8):`; вызов `transcribe` — `language="ru" if in_shell else self.lang` и добавить аргумент `initial_prompt=shell.WHISPER_PROMPT if in_shell else None`; в цикле чистки хвоста `EXIT_TAIL_RE` → `(SHELL_TAIL_RE if in_shell else EXIT_TAIL_RE)`; концовку `if text: self.paster.paste(text)` заменить на

```python
        if not text:
            return
        if in_shell:
            cmd = shell.to_command(text, shell.terminal_cwd(), self.llm, log)
            log(f"консоль: {text!r} -> {cmd!r}")
            self.paster.paste(cmd, TERMINAL_PASTE)
        else:
            self.paster.paste(text)
```

`handle_text`, между веткой `DICTATION` и `else:`:

```python
        elif self.state == SHELL:
            if t in NORM_SHELL_EXIT:
                self.flush_dictation(trim_s=1.3)
                self.set_state(COMMAND); return True
            if t == NORM_RUN:
                if conf < MIN_CONF:
                    log(f"отклонено (уверенность {conf:.2f} < {MIN_CONF}): {t}")
                    return False
                self.flush_dictation(trim_s=0.8)
                time.sleep(0.15)        # терминал должен успеть принять вставку
                self.inp.tap(E.KEY_ENTER)
                log("консоль: ввод")
                return True
```

`run`: `if self.state == DICTATION:` → `if self.state in (DICTATION, SHELL):`.

- [ ] **Step 6: Трей**

`tray.py`: в `ITEMS` после строки английской диктовки — `(("КОНСОЛЬ", None), "Консоль")`; в `ICONS` — `"КОНСОЛЬ": "utilities-terminal-symbolic"`.

- [ ] **Step 7: Проверки**

Run: `~/.local/share/nohands/venv/bin/python test_commands.py`
Expected: `ИТОГ: ПРОЙДЕН`; новые строки блока выхода и «ввод внутри фразы» — `ok`.

Run: `~/.local/share/nohands/venv/bin/python test_shell.py`
Expected: `ИТОГ: ПРОЙДЕН`.

Run: `systemctl --user restart nohands && sleep 8 && tail -n 20 ~/.local/share/nohands/logs/voice.log`
Expected: `словарь: все N слов команд распознаваемы` (без `ВНИМАНИЕ: нет в словаре vosk`). Строка `ВНИМАНИЕ: README обещает 63 фраз, в COMMANDS их 64` на этом шаге ожидаема — README правится в Task 4.

Ручная проверка голосом (запускает пользователь): открыть терминал, «проснись» → «консоль» (значок терминала в трее) → «эл эс минус эл» (в строке `ls -l`, без выполнения) → «ввод» (выполнилось) → «топ» (вставилось, короткая фраза не потерялась) → «хватит консоль».

- [ ] **Step 8: Коммит**

```bash
git add voiced.py tray.py test_commands.py
git commit -m "Режим КОНСОЛЬ: команды шелла голосом, запуск по «ввод»

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Установка и документация

**Files:**
- Modify: `bootstrap.sh`, `README.md`, `deploy/nohands`

- [ ] **Step 1: `bootstrap.sh`** — после шага whisper, перед блоком службы:

```bash
step "LLM для консоли: ollama"
# Без неё консоль работает на правилах; LLM нужна лишь фразам-намерениям
# вроде «покажи файлы в загрузках». Модель — как DEFAULTS в shell.py.
if command -v ollama >/dev/null; then
    ollama pull qwen2.5-coder:3b || echo "не скачалась: запустите ollama и повторите"
else
    echo "ollama не найдена — консоль будет работать только по правилам"
    echo "поставить: curl -fsSL https://ollama.com/install.sh | sh"
fi
```

- [ ] **Step 2: README**

1. В таблицу режимов после строки ДИКТОВКА:
   `| **КОНСОЛЬ** | команды шелла вставляются в терминал | скажите «**консоль**» |`
   и под таблицей: «Выход из консоли — «**хватит консоль**» или «**конец текста**».»
2. Строку `грамматика из 63 фраз` заменить на `грамматика из 64 фраз`.
3. Новый раздел `## Консоль` после раздела «Команды»:

```markdown
## Консоль

«**консоль**» — режим для терминала. Сказанное превращается в команду и
вставляется в строку **без Enter**: сначала видно, что получилось, и только
«**ввод**» её выполняет. «Ввод» принимается отдельной фразой — внутри
надиктованного он Enter не нажмёт.

Команду можно произносить по словам:

    эл эс минус эл эй               ls -la
    гит статус                      git status
    сиди проджектс слэш нохэндс     cd Projects/nohands
    кэт ноутс точка тэ икс тэ       cat notes.txt
    гит эд пробел точка             git add .

Символы: слэш `/`, точка `.`, тильда `~`, минус `-`, два минуса `--`,
подчёркивание `_`, равно `=`, двоеточие `:`, звёздочка `*`, доллар `$`,
кавычка `"`, пайп `|`, больше `>`, меньше `<`, амперсанд `&`. «пробел» не даёт
символу прилипнуть к предыдущему слову. Буквы флагов — по-английски: эй, би,
си, ди, эф, эйч, эл, эм, эн, эр, эс, ти, икс...

Имена файлов сверяются с каталогом терминала: «загрузки» находит `Downloads`,
«ноутс» — `notes.txt`. Каталог берётся у терминала, в который писали последним.
Имена из нескольких слов находятся только для стандартных каталогов
(«рабочий стол»); остальные придётся поправить руками.

Если первое слово не программа («покажи файлы в загрузках»), фраза уходит в
LLM. По умолчанию это `qwen2.5-coder:3b` в ollama; настраивается в
`~/.config/nohands/config.toml`:

    [shell.llm]
    backend = "ollama"          # или "none" — только правила
    model   = "qwen2.5-coder:3b"
    url     = "http://localhost:11434"

Если LLM не ответила, вставится то, что получилось по правилам, — журнал
скажет почему.
```

3. `deploy/nohands`, ветка `команды|cmds`: строку `print('РЕЖИМЫ: ...')` заменить на
   `print('РЕЖИМЫ:  проснись | сон | печатать (русский) | английский | конец текста | консоль | хватит консоль | ввод')`

- [ ] **Step 3: Проверка**

Run: `systemctl --user restart nohands && sleep 8 && grep -c "README обещает" <(tail -n 20 ~/.local/share/nohands/logs/voice.log)`
Expected: `0`.

Run: `bash -n bootstrap.sh && echo ok`
Expected: `ok`.

- [ ] **Step 4: Коммит**

```bash
git add bootstrap.sh README.md deploy/nohands
git commit -m "Консоль: ollama в bootstrap, раздел в README

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
