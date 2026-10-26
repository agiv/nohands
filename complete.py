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
# Загрузчик по умолчанию — только для команды без описания, как у Tab: без
# файла completions/<имя> он ставит _comp_complete_minimal поверх того, что
# задал сам bash_completion (time, nohup, xargs — имя программы).
complete -p "$1" >/dev/null 2>&1 || _comp_complete_load "$1" >/dev/null 2>&1
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
        p.stdout.close()
        return None
    return p.returncode, out


def _bash(mode, args, cwd, stdin=None, timeout=TIMEOUT):
    """Строки вывода SCRIPT; None — сбой, False — не ответил за timeout
    (это position запоминает)."""
    if not os.path.isdir(cwd):      # терминал в удалённом каталоге — не сбой
        return None
    env = {**os.environ, "NOHANDS_COMPLETION": COMPLETION}
    try:
        got = _run(["bash", "-c", SCRIPT, "bash", mode, *args], timeout,
                   cwd=cwd, stdin=stdin, env=env)
    except OSError as ex:
        # без ex.filename: там каталог пользователя, а журнал копится месяцами
        note(f"bash не запустился: {ex.strerror}")
        return None
    if got is None:
        # имя — только программы из PATH: первым словом может оказаться
        # сказанное пользователем («покажи»), а оно в журнал не пишется
        who = args[0] if args and args[0] in programs() else mode
        note(f"completion {who}: нет ответа за {timeout} с")
        return False
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
        if not got or got[0] != 0:
            # не в кеш: пустое множество держалось бы до установки программы,
            # а без cd, sudo и export в нём «сиди точка точка» — cd.., а
            # просьбы и позиции после sudo теряются
            return frozenset()
        _programs = (key, frozenset(got[1].split()))
    return _programs[1]


# Первые два слова команды -> с какой длины запрос с ними не ответил за
# TIMEOUT. systemctl restart — 3.75 с (completion перебирает все службы),
# systemctl --user restart — 1.49 с: без памяти каждая такая фраза теряла
# бы секунду. Короче этой длины запросы идут как раньше: systemctl --user
# отвечает за 0.03 с, и без него «рестарт» не подобрался бы (замер 05.10).
_slow = {}


def position(words, cwd):
    """Что ждут сразу после words (от начала команды): (вид, кандидаты) или
    None. Вид — files, dirs, words или program: кандидаты покрывают не
    меньше половины programs(), как после sudo, env, time, — так позиция
    имени программы узнаётся без перечня этих утилит."""
    key = tuple(words[:2])
    if len(words) >= _slow.get(key, float("inf")):
        return None
    lines = _bash("words", [*words, ""], cwd)
    if lines is False:
        _slow[key] = len(words)     # меньше прежней: от неё и длиннее отсечено выше
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
    if not words:
        return []
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


def trusted(path):
    """Программа поставлена системой: вне домашнего каталога, и пользователь
    не может её переписать. В PATH службы первыми стоят ~/.local/bin и ~/bin,
    а там свои скрипты — с тем же риском, что ./create.riverport.sh: без
    разбора аргументов скрипт на --help выполнит своё действие, и
    сказанное слово, похожее на его имя, запустило бы его."""
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    return os.path.commonpath([real, home]) != home and not os.access(real, os.W_OK)


def _help(prog):
    """Вывод `prog --help` или ''. Только системная программа из PATH (trusted):
    свой скрипт (./create.riverport.sh, ~/bin) мог бы на --help выполнить
    действие."""
    path = shutil.which(prog) if os.sep not in prog else None
    if not path or not trusted(path):
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
