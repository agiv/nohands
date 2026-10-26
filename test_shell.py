#!/usr/bin/env python3
"""Проверка режима КОНСОЛЬ без микрофона, GPU и сети.

Запуск: ~/.local/share/nohands/venv/bin/python test_shell.py

Правила сверяются с временным каталогом, где заранее известно, что лежит,
а LLM подменяется заглушкой: проверяется не её ум, а то, когда её зовут и
что бывает, если она молчит.
"""
import atexit, collections, concurrent.futures, json, os, shutil, subprocess, sys, tempfile, time, types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shell as S
import complete as C
import voiced as V
V.log = lambda *a: None     # журнал демона тесту не нужен

FAILS = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<46} -> {got!r}"
          + ("" if ok else f" (ждали {want!r})"))
    # Под pytest провал должен ронять тест, иначе он зелёный при FAIL.
    if "pytest" in sys.modules:
        assert ok, f"{name}: {got!r}, ждали {want!r}"


# Одна тестовая папка на запуск: в настоящих каталогах пользователя лежат
# его проекты и клиенты, а временные папки по одной на проверку копились в /tmp.
ROOT = tempfile.mkdtemp(prefix="nohands-test-")
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)


def sandbox(*dirs, files=()):
    d = tempfile.mkdtemp(dir=ROOT)
    for p in dirs:
        os.makedirs(os.path.join(d, p))
    for p in files:
        open(os.path.join(d, p), "w").close()
    return d


def home():
    """Каталог, где заранее известно, что лежит."""
    return sandbox("Downloads", "Desktop", "Projects/nohands", "docs", files=["notes.txt"])


# d=None, а не d: аргумент без значения по умолчанию pytest принимает за
# фикстуру и падает, не начав тест.
def test_rules(d=None):
    d = d or home()
    print("── правила ──")
    for said, want in [
        ("эл эс минус эл эй", "ls -la"),
        ("эл эс минус минус ол", "ls --all"),      # сеанс 05.10: так пишет whisper
        ("эл эс два минуса олл", "ls --all"),
        # числа словами: whisper в консоли пишет их прописью, и LLM отвечала «НЕТ»
        ("сто девяносто", "190"),
        ("сто девяноста", "190"),                  # так тоже пишет, по журналу
        ("две тысячи двадцать шесть", "2026"),
        ("хед минус эн пять", "head -n 5"),
        ("пинг сто девяносто два точка сто шестьдесят восемь точка ноль точка один",
         "ping 192.168.0.1"),
        ("восемь ноль восемь ноль", "8080"),       # по цифрам — одно число
        ("чмод семь пять пять ноутс", "chmod 755 ноутс"),
        ("чмод плюс икс ноутс", "chmod +x ноутс"),
        ("гит коммит минус эм кавычка фикс кавычка", 'git commit -m "фикс"'),
        # скобки: открывающая липнет к $ и к слову справа, закрывающая — к слову слева
        ("эхо доллар открыть скобку пи дабл ю ди закрыть скобку", "echo $(pwd)"),
        ("эхо открывающая скобка один закрывающая скобка", "echo (1)"),
        ("эхо фигурная скобка один запятая два закрыть фигурную скобку", "echo {1,2}"),
        ("эхо открыть квадратную скобку один закрыть квадратную скобку", "echo [1]"),
        ("эхо доллар открыть скобку кэт ноутс закрыть скобку", "echo $(cat ноутс)"),
        ("гит коммит минус эм одинарная кавычка фикс бага одинарная кавычка",
         "git commit -m 'фикс бага'"),
        ("эл эс точка с запятой пи дабл ю ди", "ls ; pwd"),
        ("эхо один запятая два", "echo 1,2"),
        ("эл эс звездочка вопросительный знак", "ls *?"),
        ("эхо процент эс", "echo %s"),
        ("эхо восклицательный знак восклицательный знак", "echo !!"),
        ("эхо решетка комментарий", "echo # комментарий"),
        ("эхо обратный слэш эн", "echo \\n"),
        ("эс эс эйч рут собака сто девяносто два точка сто шестьдесят восемь точка один",
         "ssh root@192.168.1"),
        ("гит эд пробил точка", "git add ."),        # whisper слышит «пробил»
        ("эл эс два минуса хелп", "ls --help"),    # «два минуса» — не число
        ("гит статус", "git status"),
        ("ди эф минус эйч", "df -h"),
        ("эл эс два минуса хелп", "ls --help"),
        ("сиди точка точка", "cd .."),
        ("гит эд пробел точка", "git add ."),
        ("гит коммит минус эм", "git commit -m"),
        ("эл эс звездочка точка тэ икс тэ", "ls *.txt"),
        ("эр эм минус эр эф звездочка", "rm -rf *"),  # «*» к флагу не липнет
        ("эхо доллар HOME", "echo $HOME"),         # латиница сохраняет регистр
        ("эхо доллар хоум", "echo $HOME"),         # whisper пишет имя кириллицей
        ("эхо доллар юзер", "echo $USER"),
        ("Доллар хоум.", "$HOME"),                 # продолжение строки после «эхо»
        ("доллар хом", "$HOME"),
        ("эхо доллар пас", "echo $PATH"),
        ("эхо доллар эдитор", "echo $EDITOR"),
        ("Git status", "git status"),              # но не в начале предложения
        ("ls -la.", "ls -la"),                     # whisper написал латиницей
        ("Сиди, загрузки.", "cd Downloads"),       # и расставил знаки
        # как whisper пишет на самом деле — из журнала живой проверки
        ("эл эс минус эль", "ls -l"),
        ("кат ноутс точка тэ икс тэ", "cat notes.txt"),
        # частые команды из ~/.bash_history
        ("мор ноутс точка тэ икс тэ", "more notes.txt"),
        ("эс си пи", "scp"),                       # а не «s cp»
        ("эс эс эйч", "ssh"),
        ("ай пи эй", "ip a"),                      # а не «ipa»
        ("докер композ ап минус ди", "docker compose up -d"),
        ("судо вэ гэ квик ап", "sudo wg-quick up"),
        ("терраформ план", "terraform plan"),
        ("клод два минуса резюм", "claude --resume"),
        # так whisper пишет на самом деле — журнал 26.09, 19:28–19:30
        ("клоуд", "claude"), ("клоут", "claude"), ("клауде", "claude"),
        ("клауда", "claude"), ("клада", "claude"), ("клад", "claude"),   # 19:32
        ("ходе точка", "code ."),
        ("докер пс минус а", "docker ps -a"),
        ("дукер пэс минус эй", "docker ps -a"),
        ("докер, контейнер, прюн", "docker container prune"),
        ("докер, контейнер, прум", "docker container prune"),   # 19:46
        ("х топ", "htop"), ("и чтоп", "htop"),                  # 19:47
        ("аж топ", "htop"), ("эм цэ эдит", "mcedit"),           # 20:19
        ("мс", "mc"), ("эмси", "mc"), ("эм си", "mc"),           # 21:01
        ("эм цэ", "mc"), ("эм це", "mc"), ("эмцэ", "mc"), ("эм це эдит", "mcedit"),
        ("эм си эдит ноутс точка тэ икс тэ", "mcedit notes.txt"),
    ]:
        check(said, S.rules(said, d), want)


def test_user():
    print("── текущий пользователь ──")
    real = S.current_user
    try:
        for user, said, want in [
            ("streiber", "эс эс эйч штрейбер собака хост", "ssh streiber@хост"),
            ("streiber", "эс эс эйч стрейбер собака хост", "ssh streiber@хост"),
            ("petrov", "эс эс эйч петров собака хост", "ssh petrov@хост"),
            ("streiber", "эхо стример", "echo стример"),     # похожее слово — не имя
            ("streiber", "чоун штрейбер ноутс", "chown streiber ноутс"),
        ]:
            S.current_user = lambda: user
            check(f"{user}: {said}", S.rules(said, sandbox()), want)
    finally:
        S.current_user = real


def test_paths(d=None):
    d = d or home()
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
    # whisper пишет одно имя несколькими словами — ищем их вместе
    check("имя папки в два слова", S.rules("сиди но хэндс", os.path.join(d, "Projects")),
          "cd nohands")
    check("имя в два слова после слэша", S.rules("сиди проджектс слэш но хэндс", d),
          "cd Projects/nohands")
    # тот самый случай из журнала: «атлас медиа» в два слова и «-sion» на слух
    cm = sandbox("atlasmedia/fusion/atlas-server-builder", "atlasmedia/music")
    for said, want in [
        ("сиди атлас медиа слэш фьюзжен", "cd atlasmedia/fusion"),     # -sion на слух
        # фразы из журнала: cd по буквам, путь без «слэш», имя через дефисы
        ("цд, атлас медиа фьюжн", "cd atlasmedia/fusion"),
        ("сэдэ атлас медиа слэш фьюжн", "cd atlasmedia/fusion"),
        ("цэ дэ, атлас медиа слэш фьюжн слэш атлас сервер билдер",
         "cd atlasmedia/fusion/atlas-server-builder"),
    ]:
        check(said, S.rules(said, cm), want)
    # запуск программы: имя в несколько слов после ./ — тоже по диску
    sb = sandbox(files=["server-builder", "app"])
    for said, want in [
        ("точка слэш сервер билдер", "./server-builder"),
        ("точка слэш сервер билдер минус эл", "./server-builder -l"),
        ("точка слэш апп", "./app"),
    ]:
        check(said, S.rules(said, sb), want)
    # журнал 28.09, 09:29: whisper коверкает «create», а .sh не произносят.
    # Имена выдуманы, искажения — того же рода, что в журнале.
    op = sandbox(files=["create.riverport.sh", "create.north-harbor-studio.sh",
                        "create.greenfield.sh", "create.test.sh", "delete.sh",
                        "deploy.sh", "list.sh", "status.sh", "test.sh",
                        "upgrade.sh", "update.sh"])
    for said, want in [
        ("точка слэш крит ривер порт", "./create.riverport.sh"),
        ("точка слэш креата рива пот", "./create.riverport.sh"),
        ("точка слэш кряти, точка рива парт", "./create.riverport.sh"),
        ("точка слэш крейт точка риверпорт", "./create.riverport.sh"),
        ("точка слэш деплой", "./deploy.sh"),
        # журнал 28.09, 11:00: «апгрейд» по буквам — apgreyd, 0.57 с upgrade
        ("точка слэш апгрейд", "./upgrade.sh"),
        ("точка слэш апдейт", "./update.sh"),
        # ни на что не похоже — слова остаются как сказаны
        ("точка слэш сервер билдер", "./сервер билдер"),
    ]:
        check(said, S.rules(said, op), want)
    # журнал 28.09, 16:00: одиночный «слэш» вставлял '', «точка слэш» — точку.
    # Конечный слэш — «дальше внутрь», после него Tab или имя.
    for said, want in [
        ("слэш", "/"), ("точка слэш", "./"), ("точка точка слэш", "../"),
        ("тильда слэш", "~/"), ("сиди тильда слэш", "cd ~/"),
        ("сиди проджектс слэш", "cd Projects/"),
    ]:
        check(said, S.to_command(said, d, None), want)
    # журнал 28.09, 14:52: английское имя каталога whisper пишет русскими
    # словами, сходство 0.52–0.69 при строгом пороге 0.7. Имя выдумано,
    # искажения — того же рода.
    far = sandbox("in-province-cli", "open-api", "player-api", "help-base",
                  "ansible", "video-converter", "client", "license")
    for said in ("сиди ин провинция селой", "сиди ин про винс клей",
                 "сиди ин провинции с клей"):
        check(said, S.rules(said, far), "cd in-province-cli")
    # два похожих каталога — угадывать нельзя, слова остаются как сказаны
    twins = sandbox("in-province-cli", "in-province-api")
    check("два похожих каталога", S.rules("сиди ин провинция селой", twins),
          "cd ин провинция селой")
    check("два слова — не имя", S.rules("эхо но хэндс", os.path.join(d, "Projects")),
          "echo но хэндс")
    # не нашлось ничего — слова остаются как сказаны, пробел не теряется
    check("рабочий стол в пустом каталоге", S.rules("сиди рабочий стол", sandbox()),
          "cd 'рабочий стол'")


def test_home_dirs():
    print("── домашние папки по-русски ──")
    # ~/Downloads бывает симлинком на ~/Загрузки — берём настоящее имя
    d = sandbox("Загрузки", "Downloads", "Рабочий стол")
    check("есть «Загрузки» — они, а не Downloads",
          S.rules("сиди загрузки", d), "cd Загрузки")
    check("папка с пробелом — в кавычках",
          S.rules("сиди рабочий стол", d), "cd 'Рабочий стол'")
    real, S.HOME = S.HOME, d
    try:
        check("промпт: русская папка", S.home_dir("загрузки"), "~/Загрузки")
        check("промпт: с пробелом", S.home_dir("рабочий стол"), "~/'Рабочий стол'")
        check("промпт: русской нет — английская", S.home_dir("видео"), "~/Videos")
        p = S.prompt_for("покажи файлы", d)
        check("промпт: пример с настоящей папкой", "-> ls ~/Загрузки\n" in p, True)
        check("промпт: ~/Downloads не упоминается", "~/Downloads" in p, False)
    finally:
        S.HOME = real


def test_runnable():
    print("── программа ли это ──")
    for cmd, want in [("ls -la", True), ("cd ..", True), ("покажи файлы", False), ("", False)]:
        check(repr(cmd), S.runnable(cmd), want)


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
    # whisper пишет как слышит: «комит» с одной «м», «к» вместо «c»
    sub = ["commit", "config", "clone", "describe", "diff"]
    check("по звуку: комит — commit", S._closest("комит", sub, *S.WORD_FIT)[0], "commit")
    check("по звуку: дискрибе — describe", S._closest("дискрибе", sub, *S.WORD_FIT)[0],
          "describe")
    check("по произношению: дискрайб — describe",
          S._closest("дискрайб", sub, *S.WORD_FIT, spoken=True)[0], "describe")
    # как пишет whisper: конец оглушён, безударное «е» — «и» (сеанс 05.10)
    for said in ("дискрайп", "дискриби"):
        check(f"  {said} — describe", S._closest(said, sub, *S.WORD_FIT, spoken=True)[0],
              "describe")
    check("  рисет — reset", S._closest("рисет", ["reset", "restore", "rebase"], *S.WORD_FIT,
                                         spoken=True)[0], "reset")
    check("  программы — по написанию: файлы не file",
          S._closest("файлы", ["file", "filefrag", "find"], *S.PROGRAM_FIT, variants=True),
          (None, 0.0))
    check("  совпало по звуку — не точное",
          S._closest("ты", ["tty", "true"], *S.PROGRAM_FIT, variants=True)[1] < 1.0, True)


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
# Подкоманды — сразу, а дальше зависает: так systemctl restart (3.75 с,
# список всех служб) при быстром systemctl --user (0.03 с) — замер 05.10.
SLOW_COMPLETION = r'''
_slowctl() {
    ((COMP_CWORD > 1)) && sleep 30
    COMPREPLY=($(compgen -W "restart status" -- "${COMP_WORDS[COMP_CWORD]}"))
}
complete -F _slowctl slowctl
'''
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
    # tidectl — свой скрипт пользователя, аргументов не разбирает: запуск
    # оставляет файл <каталог>/ran
    bins = {"atlasctl": "#!/bin/sh\n", "riverctl": "#!/bin/sh\n",
            "riverportd": "#!/bin/sh\n", "slowctl": "#!/bin/sh\n",
            "harborctl": HARBOR, "slowhelp": "#!/bin/sh\nsleep 30\n",
            "tidectl": f"#!/bin/sh\ntouch {os.path.join(d, 'ran')}\n"}
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
        # time описан в самом bash_completion; загрузчик по умолчанию без файла
        # completions/time заменил бы его на «файлы», а Tab его не зовёт
        check("после time — имя программы", C.position(["time"], work)[0], "program")
        check("у cd — каталог", C.position(["cd"], work)[0], "dirs")
        check("каталога нет — не знаю", C.position(["atlasctl"], os.path.join(d, "gone")), None)
        check("ключи из completion", C.options(["atlasctl"]), ["--dry-run", "--verbose"])
        check("ключи из man", C.options(["riverctl"]), ["--follow", "--verbose"])
        # bin песочницы пользователь может переписать — как ~/bin; --help у
        # таких не зовём, поэтому системность здесь подменена
        with mock.patch.object(C, "trusted", return_value=True):
            check("ключи из --help", C.options(["harborctl"]), ["--quiet", "--region="])
            t = time.time()
            check("--help завис — ключей нет", C.options(["slowhelp"]), [])
            check("  ждали не дольше таймаута", time.time() - t < C.HELP_TIMEOUT + 1, True)
        check("свой скрипт — --help не зовём", C.options(["tidectl"]), [])
        check("  и он не запускался", os.path.exists(os.path.join(d, "ran")), False)
        check("bin песочницы — не системный",
              C.trusted(os.path.join(d, "bin", "harborctl")), False)
        check("/usr/bin/env — системный", C.trusted("/usr/bin/env"), True)
        t = time.time()
        check("completion завис — не знаю", C.position(["slowctl", "restart"], work), None)
        check("  ждали не дольше таймаута", time.time() - t < C.TIMEOUT + 1, True)
        msgs = C.drain()
        check("  оба сбоя — в журнал",
              [any("slowhelp" in m for m in msgs), any("slowctl" in m for m in msgs)],
              [True, True])
        t = time.time()
        check("  второй раз — сразу «не знаю»", C.position(["slowctl", "restart"], work), None)
        check("  и дальше в той же команде", C.position(["slowctl", "restart", "x"], work), None)
        check("  оба без ожидания", time.time() - t < 0.2, True)
        check("  тот же сбой второй раз не пишется", C.drain(), [])
        # systemctl --user отвечает за 0.03 с и после зависшего
        # systemctl --user restart: без него «рестарт» не подбирался бы
        check("  короткий запрос по-прежнему отвечает", C.position(["slowctl"], work),
              ("words", ["restart", "status"]))
        # compgen не ответил: пустой ответ — не в кеш, иначе до установки
        # программы не было бы ни cd, ни sudo
        with mock.patch.object(C, "_programs", (None, frozenset())):
            with mock.patch.object(C, "_run", return_value=None):
                first = C.programs()
            check("compgen сбойнул — пусто и не в кеш", (len(first), "cd" in C.programs()),
                  (0, True))
        check("ключи без слов — пусто, без исключения", C._find_options([]), [])
        with mock.patch.object(C, "COMPLETION", os.path.join(d, "missing")):
            check("нет bash-completion — не знаю", C.position(["atlasctl"], work), None)
        check("  и об этом одна строка", len(C.drain()), 1)


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
            ("атлас ктл минус минус вербоус", "atlasctl --verbose"),  # сеанс 05.10
            ("ривер ктл два минуса фоллоу", "riverctl --follow"),     # из man
            ("атлас ктл два минуса хелп", "atlasctl --help"),     # в completion нет --help
            ("сиди деплой стейджинг", "cd deploy-staging"),           # cd — тоже позиция dirs
            ("эл эс пайп атлас ктл статус", "ls | atlasctl status"),
            ("судо атлас ктл статус", "sudo atlasctl status"),
            # латиницу нечётко не подбираем: riverport — не riverportd (0.95),
            # push — не pushd, stop — не top, clone — не rclone
            ("riverport", "riverport"),
            ("пуш", "push"), ("стоп", "stop"), ("клон", "clone"),
        ]:
            check(said, S.rules(said, work), want)
        with mock.patch.object(C, "trusted", return_value=True):
            check("харбор ктл два минуса регион", S.rules("харбор ктл два минуса регион", work),
                  "harborctl --region")                         # из --help
        # python3 --help разбирается в «--help-all:»
        with mock.patch.object(S.complete, "options", return_value=["--help-all:"]):
            check("ключ с двоеточием на конце", S._option("--хелп-олл", ["tidectl"]),
                  "--help-all")
        # первое слово — не программа: Tab предложил бы файлы, bash не нужен
        # (замер: 9 запусков, 0.30 с на эту фразу)
        with mock.patch.object(S.complete, "position", wraps=S.complete.position) as pos:
            S.rules("покажи последние три файла из документов", work)
        check("просьба словами — completion не спрашивают", pos.call_count, 0)
        # слова намерения ниже порога (плавать -> pacat 0.73)
        for w in ("покажи", "сделай", "плавать", "сколько", "останови", "открой"):
            check(f"«{w}» — не программа", S.rules(w + " файлы", work).split()[0], w)
        # нечёткое имя и кириллица в аргументах — просьба словами, в LLM:
        # «проверь» — prove 0.91; короткое слово нечётко не подбирается
        # («там» — tasm 0.86, «ты» — tty 0.80)
        for said, first in [("проверь место на диске", "проверь"),
                            ("там лежат логи удали их", "там"), ("ты где", "ты")]:
            check(f"«{first} …» — не программа", S.rules(said, work).split()[0], first)
        check("нечёткое имя, аргументы подобраны", S.rules("риверпорт ноутс точка тэ икс тэ", work),
              "riverportd notes.txt")
        llm = Stub("df -h")
        check("«проверь место на диске» — в LLM",
              S.to_command("проверь место на диске", work, llm, lambda *a: None), "df -h")
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


def test_chatter(d=None):
    d = d or home()
    print("── разговор рядом — не команда ──")
    quiet, no = (lambda *a: None), Stub("НЕТ")
    # первое слово транслитом точно совпало с программой (net, test, set,
    # more), а за ним одни русские слова — это не команда
    for said in ("нет я не знаю", "тест прошёл нормально", "сеть опять не работает",
                 "море было тёплое"):
        check(said, S.to_command(said, d, no, quiet), "")
    # а здесь аргумент подобран, имя из WORDS или аргумент один
    for said, want in [("гит коммит минус эм фикс", "git commit -m фикс"),
                       ("эхо привет", "echo привет"), ("мкдир папка", "mkdir папка"),
                       ("нано файл", "nano файл")]:
        check(said, S.to_command(said, d, no, quiet), want)
    check("  LLM спрошена только о разговоре", len(no.asked), 4)


class Stub:
    """LLM-заглушка: запоминает вопросы, отвечает заданным текстом или падает."""
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.asked = reply, error, []

    def complete(self, prompt):
        self.asked.append(prompt)
        if self.error:
            raise self.error
        return self.reply


def test_llm(d=None):
    d = d or home()
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
    llm = Stub("cat README.md")
    check("одно слово — LLM не зовут, вставляю услышанное",
          S.to_command("небо", d, llm, quiet), "небо")
    # транслит «море» — ровно more: имя программы подбирается и в одно слово
    check("одно слово — программа по транслиту", S.to_command("море", d, llm, quiet), "more")
    check("одно слово — имя файла из каталога", S.to_command("ноутс точка тэ икс тэ", d, llm, quiet),
          "notes.txt")
    check("хвост с флагом — как есть", S.to_command("минус эл", d, llm, quiet), "-l")
    check("хвост с пайпом — как есть", S.to_command("пайп греп питон", d, llm, quiet), "| grep python3")
    check("лишний слог спереди — отбрасываю", S.to_command("в клауде", d, llm, quiet), "claude")
    check("  и с аргументами", S.to_command("и гит статус", d, llm, quiet), "git status")
    check("  и вопросов не было", len(llm.asked), 0)
    check("LLM отказалась — вставлять нечего",
          S.to_command("ну да им нельзя плавать", d, Stub("НЕТ"), quiet), "")
    check("LLM ответила не программой — результат правил",
          S.to_command("покажи облако", d, Stub("cloudflare"), quiet), "покажи облако")
    check("«докс» — это docs, «кс» не только x",
          S.rules("эл эс докс", d), "ls docs")
    check("«лис» и «лез» — тоже less", [S.to_command(w, d, None, quiet) for w in ("лис", "лез")], ["less", "less"])
    check("«лэс» — это less", S.to_command("лэс", d, None, quiet), "less")
    check("ответ «$ cmd» без приглашения", S.clean_reply("$ ls -l\n"), "ls -l")
    check("ответ в обратных кавычках", S.clean_reply("`pwd`"), "pwd")
    check("ответ с пояснением и двоеточием", S.clean_reply("Вот команда:\nls -la"), "ls -la")
    check("команда в `…` посреди фразы",
          S.clean_reply("Используйте `du -sh *` для этого"), "du -sh *")
    check("подстановка `…` в команде остаётся", S.clean_reply("echo `date`"), "echo `date`")
    llm = Stub("ls")
    check("одна пунктуация — вставлять нечего",
          [S.to_command(t, d, llm, quiet) for t in ("...", "—", ". .")], ["", "", ""])
    check("  и LLM не зовут", len(llm.asked), 0)


def test_config():
    print("── настройка ──")
    quiet = lambda *a: None
    def cfg(text):
        p = os.path.join(sandbox(), "config.toml")
        with open(p, "w") as f:
            f.write(text)
        return p
    llm = S.load_llm(quiet, path="/нет/такого/файла.toml")
    check("файла нет — ollama по умолчанию",
          (type(llm).__name__, llm.model, llm.url),
          ("Ollama", "qwen2.5-coder:7b", "http://localhost:11434"))
    llm = S.load_llm(quiet, cfg('[shell.llm]\nmodel = "qwen2.5-coder:3b"\n'))
    check("своя модель", llm.model, "qwen2.5-coder:3b")
    check("num_gpu не задан — раскладку выбирает ollama", llm.options, {})
    llm = S.load_llm(quiet, cfg('[shell.llm]\nnum_gpu = 20\n'))
    check("num_gpu — в опции ollama", llm.options, {"num_gpu": 20})
    check("backend = none", S.load_llm(quiet, cfg('[shell.llm]\nbackend = "none"\n')), None)
    check("неизвестный backend — выключена",
          S.load_llm(quiet, cfg('[shell.llm]\nbackend = "gpt"\n')), None)
    check("битый toml — по умолчанию",
          S.load_llm(quiet, cfg("[shell.llm\n")).model, "qwen2.5-coder:7b")


def test_cwd():
    print("── каталог терминала ──")
    check("существующий каталог", os.path.isdir(S.terminal_cwd()), True)


def test_quoting():
    print("── кавычки ──")
    home = sandbox("Рабочий стол", files=["file(1).txt"])
    real, S.HOME = S.HOME, home
    try:
        # в кавычках тильда не раскрывается — она должна остаться снаружи
        check("~/ с пробелом", S.rules("сиди тильда слэш рабочий стол", home),
              "cd ~/'Рабочий стол'")
    finally:
        S.HOME = real
    check("скобки в настоящем имени", S.rules("кэт file(1).txt", home), "cat 'file(1).txt'")
    check("переменная не в кавычках", S.rules("эхо доллар пи эй ти эйч", home), "echo $path")


class FakeDaemon:
    """Ровно то, что трогает handle_text в КОНСОЛИ, — без звука и uinput."""
    SHELL_IDLE_S = V.Daemon.SHELL_IDLE_S
    def __init__(self, pasted):
        self._shell_used = time.time()
        self.state, self._last_cmd, self._last_t = V.SHELL, None, 0.0
        self.pasted, self.keys, self.pastes, self.line = pasted, [], [], ""
        self.dropped = False
        self.paster, self.sent = self, []
        self.inp = self.ears = self
        self._shell_pending = collections.deque()
    _shell_results = V.Daemon._shell_results
    _drop_shell_jobs = V.Daemon._drop_shell_jobs
    _drop_exit_echo = V.Daemon._drop_exit_echo
    _paste_shell = V.Daemon._paste_shell
    said_at = 0.0
    def now(self):
        return 0.0
    def tail_s(self):
        return 0.0
    def flush_dictation(self, trim_s=0.0):
        # pasted — фраза только что ушла в разбор и ещё не в строке
        if self.pasted:
            self._shell_pending.append((concurrent.futures.Future(), "ls", False, 0.0))
        return self.pasted
    def drop_buffer(self):
        self.dropped = True
    def paste(self, text, keys):
        self.sent.append(text)
    def tap(self, *codes):
        self.keys.append(codes)
    def set_state(self, new, note=""):
        self.state = new


def test_run_phrase():
    print("── «ввод» ──")
    d = FakeDaemon(pasted=False)
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    check("буфер пуст — Enter", d.keys, [(V.E.KEY_ENTER,)])
    # «ввод» сказан раньше, чем допечаталась фраза: её ещё никто не видел
    d = FakeDaemon(pasted=True)
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    check("фраза ещё не в строке — Enter не жмём", d.keys, [])
    # «нет», пока фраза разбирается: её и отменяют, а стирать ещё нечего
    with mock.patch.object(V, "log"):
        V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("«нет» до вставки — отменено, не стёрто", (len(d._shell_pending), d.keys), (0, []))
    d = FakeDaemon(pasted=False)
    V.Daemon.handle_text(d, V.NORM_RUN, 0.5)
    check("низкая уверенность — Enter не жмём", d.keys, [])


def test_whisper_run():
    print("── whisper пишет «ввод» ──")
    # Повторённый «ввод» whisper склеивает в одну строку («вот, вот»). Она не
    # равна ни одному слову набора, но командой не бывает: в LLM (4 с) её
    # нести незачем.
    for text in ("вот", "Ввод.", "вот, вот", "вот вот", "Вод, вот, ввод"):
        check(f"{text!r} — это «ввод»", V.whisper_says_run(text), True)
    for text in ("", "   ", "вот так", "вот ls", "ввод файла", "вотвот"):
        check(f"{text!r} — не «ввод»", V.whisper_says_run(text), False)


def test_shell_idle():
    print("── КОНСОЛЬ без дела уходит в СОН ──")
    # колонки не жмут «ввод» в КОНСОЛИ, про которую забыли; а в КОМАНДАХ
    # «ввод» тоже Enter, поэтому выход — в СОН
    idle = V.Daemon.SHELL_IDLE_S
    with mock.patch.object(V, "log"):
        d = FakeDaemon(pasted=False)
        d._shell_used = time.time() - idle + 5
        V.Daemon._shell_idle(d)
        check("меньше пяти минут — остаётся", d.state, V.SHELL)
        d._shell_used = time.time() - idle - 1
        V.Daemon._shell_idle(d)
        check("пять минут без клавиш — СОН", d.state, V.SLEEP)
        d = FakeDaemon(pasted=False)
        d._shell_used = time.time() - idle - 1
        V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
        V.Daemon._shell_idle(d)
        check("«ввод» продлевает", d.state, V.SHELL)
        d = FakeDaemon(pasted=False)
        d._shell_used = time.time() - idle - 1
        V.Daemon.handle_text(d, V.NORM_RUN, 0.5)
        V.Daemon._shell_idle(d)
        check("отклонённый «ввод» не продлевает", d.state, V.SLEEP)
        d = FakeDaemon(pasted=False)
        d._shell_used = time.time() - idle - 1
        V.Daemon.handle_text(d, "вверх", 1.0)
        V.Daemon._shell_idle(d)
        check("клавиша продлевает", d.state, V.SHELL)
        d = FakeDaemon(pasted=False)
        d._shell_used = time.time() - idle - 1
        V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
        V.Daemon._shell_idle(d)
        check("«нет» продлевает", d.state, V.SHELL)
    check("пять минут — это 300 с", idle, 300.0)


def test_prompt_echo():
    print("── эхо подсказки whisper ──")
    # на почти тихом фрагменте whisper выдаёт свою подсказку дословно — было
    # в журнале и ушло в терминал целой строкой
    check("подсказка целиком", S.prompt_echo(S.WHISPER_PROMPT), True)
    check("подсказка без хвоста", S.prompt_echo(S.WHISPER_PROMPT.rsplit(",", 1)[0]), True)
    check("настоящая команда из подсказки", S.prompt_echo("эл эс минус эл эй"), False)
    check("обычная команда", S.prompt_echo("кэт шэл точка пай"), False)
    check("хвост подсказки из нескольких пунктов",
          S.prompt_echo("кэт тэ, судо эпт, два минуса хелп, эм си"), True)   # 21:07
    check("команда с одним пунктом подсказки", S.prompt_echo("судо эпт инсталл эм си"), False)


def test_ollama_warm():
    print("── ollama: модель держится в памяти ──")
    sent, loaded = [], []
    class Reply:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body
    def fake_urlopen(req, timeout):
        if isinstance(req, str):            # /api/ps: что уже загружено
            return Reply(json.dumps({"models": loaded}).encode())
        sent.append((json.loads(req.data), timeout))
        return Reply(b'{"response": "ls"}')
    real, S.urllib.request.urlopen = S.urllib.request.urlopen, fake_urlopen
    real_free, S.vram_free_mb = S.vram_free_mb, lambda: None
    try:
        llm = S.Ollama("m", "http://x")
        llm.complete("вопрос")
        # холодная загрузка — ~27 с, а выгружает ollama через 5 минут простоя
        check("keep_alive в запросе", sent[0][0].get("keep_alive"), S.KEEP_ALIVE)
        llm.warm()
        check("прогрев — запрос без промпта", "prompt" in sent[1][0], False)
        check("прогрев ждёт загрузку дольше таймаута", sent[1][1] > llm.timeout, True)
        # другое число слоёв на GPU — ollama перезагружает модель
        llm = S.Ollama("m", "http://x", options={"num_gpu": 20})
        llm.warm(); llm.complete("вопрос")
        check("прогрев с теми же слоями", sent[2][0]["options"], {"num_gpu": 20})
        check("запрос с теми же слоями", sent[3][0]["options"],
              {"temperature": 0, "num_gpu": 20})
        # 27.09: браузер занял видеопамять, 20 слоёв влезли впритык, и
        # whisper падал с out of memory на каждой фразе
        S.vram_free_mb = lambda: 3474
        llm = S.Ollama("m", "http://x", options={"num_gpu": 20})
        check("памяти мало — слоёв меньше", llm.warm(), 16)
        check("  и запрос с теми же слоями", (llm.complete("в"), sent[-1][0]["options"]["num_gpu"])[1], 16)
        S.vram_free_mb = lambda: 9000
        check("памяти много — не больше, чем в настройках",
              S.Ollama("m", "http://x", options={"num_gpu": 20}).warm(), 20)
        S.vram_free_mb = lambda: 500
        loaded.append({"name": "m", "size_vram": 3000 * 2**20})
        check("своя загруженная модель — память её",
              S.Ollama("m", "http://x", options={"num_gpu": 20}).warm(), 17)
        S.vram_free_mb = lambda: 500
        loaded.clear()
        check("памяти нет — целиком на процессоре",
              S.Ollama("m", "http://x", options={"num_gpu": 20}).warm(), 0)
        check("num_gpu не задан — не трогаем", S.Ollama("m", "http://x").warm(), None)
    finally:
        S.urllib.request.urlopen = real
        S.vram_free_mb = real_free


def test_llm_after_whisper():
    print("── LLM грузится после whisper ──")
    order = []
    class LLM:
        max_gpu = 20
        def unload(self): order.append("выгрузка LLM"); return True
        def warm(self): order.append("замер и загрузка LLM"); return 14
    class Whisper:
        def __init__(self, *a, **k): order.append("whisper")
    d = type("D", (), {})()
    d.llm, d.whisper, d._wlock = LLM(), None, V.threading.Lock()
    d._ensure_whisper = lambda: V.Daemon._ensure_whisper(d)
    import faster_whisper
    real = faster_whisper.WhisperModel, V.whisper_settings
    faster_whisper.WhisperModel = Whisper
    V.whisper_settings = lambda log=None: ("medium", "cuda", "int8_float16")
    try:
        V.Daemon.warm_llm(d)            # вход в КОНСОЛЬ раньше, чем догрузился whisper
        for _ in range(50):
            if len(order) == 3:
                break
            V.time.sleep(0.02)
    finally:
        faster_whisper.WhisperModel, V.whisper_settings = real
    # прежняя LLM выгружена, whisper в памяти — и только тогда её замер
    check("порядок загрузки", order, ["выгрузка LLM", "whisper", "замер и загрузка LLM"])


def test_undo():
    print("── «нет» ──")
    BS = (V.E.KEY_BACKSPACE,)
    d = FakeDaemon(pasted=False)
    d.pastes = [("ls -a", ""), (" -l", "text")]
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("стирает последнюю вставку", d.keys, [BS] * 3)
    check("  недопечатанное отброшено", d.dropped, True)
    d.keys, d._last_cmd = [], None           # повтор той же фразы разрешён не сразу
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("второй раз — предыдущую", d.keys, [BS] * 5)
    d.keys, d._last_cmd = [], None
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("стирать нечего — ничего не жмёт", d.keys, [])
    d = FakeDaemon(pasted=False)
    d.pastes = [("ls", "")]
    V.Daemon.handle_text(d, V.NORM_UNDO, 0.5)
    check("низкая уверенность — не стирает", d.keys, [])
    d = FakeDaemon(pasted=False)
    d.pastes = [("ls", "")]
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    check("после «ввод» стирать нечего", d.pastes, [])
    d = FakeDaemon(pasted=False)
    d.pastes = [("эхо", "")]
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("кириллица — по символу, не по байту", d.keys, [BS] * 3)


def test_exit_tail():
    print("── хвост команды выхода ──")
    # vosk засчитал выход, а whisper услышал «конец консоли»: LLM сделала
    # из этого exit и вставила в терминал
    def strip(text, *heard):
        return V.strip_tail(text, set(heard), in_shell=True)
    check("конец консоли", strip("конец консоли", "хватит консоль"), "")
    check("хвост после команды", strip("гит статус, хватит консоли.", "unk хватит консоль"), "гит статус")
    check("нет в хвосте не трогаем", strip("гит статус нечто", "нет"), "гит статус нечто")
    # числа из клавишных фраз («эф один», «вверх пять раз») — не хвост
    check("число в конце", strip("сто девяносто один", "эф один"), "сто девяносто один")
    check("пять в конце", strip("двести пять", "вверх пять раз"), "двести пять")
    check("клавишная фраза целиком", strip("эл эс вверх два раза", "вверх два раза"), "эл эс")
    # фраза, которой vosk не слышал, — это слова команды, а не хвост
    check("нет — слово команды", strip("эхо нет"), "эхо нет")
    check("вниз — слово команды", strip("эхо вниз", "ввод"), "эхо вниз")
    check("текст — слово команды", strip("кат текст"), "кат текст")
    check("консоль — слово команды", strip("эхо консоль", "вверх"), "эхо консоль")
    # vosk отклонил «ввод» барьером, а whisper его записал
    check("отклонённый ввод", strip("эл эс ввод", "ввод"), "эл эс")
    check("диктовка: текст в конце", V.strip_tail("пришли текст", set(), in_shell=False), "пришли текст")
    check("диктовка: выход", V.strip_tail("пришли текст. Конец текста.", {"конец текста"}, in_shell=False),
          "пришли текст")


def test_exit_echo():
    print("── выход из КОНСОЛИ не вставляет себя ──")
    # 25.10: фрагмент закрылся паузой раньше, чем vosk отдал «хватит консоль»
    # (партиал в этот миг уже пуст), whisper услышал в нём «хуай», и оно
    # встало в строку уже в КОМАНДАХ. Речь, которая заходит за начало фразы
    # выхода, — это она сама.
    with mock.patch.object(V, "log"):
        d = FakeDaemon(pasted=False)
        said, echo = concurrent.futures.Future(), concurrent.futures.Future()
        d._shell_pending.extend([(said, "эл эс", False, 3.1), (echo, "хуай", False, 4.6)])
        d.said_at = 4.2
        V.Daemon.handle_text(d, V.NORM_SHELL_EXIT[0], 1.0)
        for job, cmd in ((said, "ls"), (echo, "хуай")):
            if not job.cancelled():
                job.set_result(cmd)
        V.Daemon._shell_results(d, wait=True)
    check("сказанное до выхода встаёт, сам выход — нет", d.sent, ["ls"])
    check("режим — КОМАНДЫ", d.state, V.COMMAND)


def test_glue_pastes():
    print("── вставки подряд ──")
    d = FakeDaemon(pasted=False)
    V.Daemon._paste_shell(d, "ls -a")
    check("первая — без пробела", d.sent, ["ls -a"])
    V.Daemon._paste_shell(d, "-l")
    check("следующая — через пробел", d.sent, ["ls -a", " -l"])
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    check("«нет» стирает и пробел", d.keys, [(V.E.KEY_BACKSPACE,)] * 3)
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    V.Daemon._paste_shell(d, "pwd")
    check("после «ввод» — снова без пробела", d.sent[-1], "pwd")
    # «вверх» достал из истории «./status.sh 192»: «1» вплотную давало 1921
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    V.Daemon.handle_text(d, "вверх", 1.0)
    V.Daemon._paste_shell(d, "1")
    check("после «вверх» — через пробел", d.sent[-1], " 1")
    # «пробел» сказан отдельно — он и вставляется, второго следом не нужно
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    V.Daemon._paste_shell(d, "./status.sh")
    V.Daemon._paste_shell(d, "", lead=True)
    check("«пробел» один — пробел", d.sent[-1], " ")
    V.Daemon._paste_shell(d, "1")
    check("после «пробел» — без второго", d.sent[-1], "1")
    V.Daemon.handle_text(d, V.NORM_UNDO, 1.0)
    V.Daemon._paste_shell(d, "2")
    check("«нет» вернул строку без пробела", d.sent[-1], "2")
    # «пробел один» в пустой строке: пробел просили — он и будет
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    V.Daemon._paste_shell(d, "1", lead=True)
    check("«пробел …» в начале — пробел", d.sent[-1], " 1")
    # Tab дописал каталог до «/» — следующее имя вплотную
    V.Daemon.handle_text(d, V.NORM_RUN, 1.0)
    V.Daemon._paste_shell(d, "cd Proj")
    V.Daemon.handle_text(d, "таб", 1.0)
    V.Daemon._paste_shell(d, "nohands")
    check("после «таб» — без пробела", d.sent[-1], "nohands")
    check("«Пробел, один» — пробел впереди", S.leading_space("Пробел, один"), True)
    check("«пробил» — так пишет whisper", S.leading_space("пробил"), True)
    check("«через пробел» — не впереди", S.leading_space("через пробел"), False)
    # настоящий Paster: одиночный пробел он молча выбрасывал (журнал 28.09, 16:24)
    taps = []
    p = V.Paster(types.SimpleNamespace(tap=lambda *k: taps.append(k)))
    p._save, p._put = (lambda: None), (lambda *a: None)
    with mock.patch.object(V.time, "sleep"):
        p.paste(" ", V.TERMINAL_PASTE)
    if p.timer:             # иначе через секунду полез бы в настоящий буфер
        p.timer.cancel()
    check("Paster вставляет одиночный пробел", taps, [V.TERMINAL_PASTE])


def test_shell_no_fallback():
    print("── КОНСОЛЬ: whisper без запасных температур ──")
    # журнал 28.09: 66 сбоев CUDA out of memory, все в КОНСОЛИ рядом с LLM.
    # Запасные температуры — пять гипотез разом: пик 1872 МБ против 1200
    class Whisper:
        def transcribe(self, audio, **kw):
            self.kw = kw
            return [], None
    d = types.SimpleNamespace(state=V.SHELL, lang="ru", whisper=Whisper(), noise_gate=100,
                              ears=types.SimpleNamespace(heard=set(), now=lambda: 0.0),
                              MIN_SHELL_S=V.Daemon.MIN_SHELL_S, MIN_LOUD_S=V.Daemon.MIN_LOUD_S,
                              silence=0.0, speech=1.0,
                              buf=[V.np.ones(2 * V.RATE, dtype=V.np.int16)])
    with mock.patch.object(V, "log"):
        V.Daemon.flush_dictation(d)
    check("температура одна — 0", d.whisper.kw.get("temperature"), 0.0)


def test_click_is_not_speech():
    print("── щелчок не уходит в whisper ──")
    # в буфере всегда 0.9 с тишины после фразы, так что длина буфера щелчка не
    # отличает; считается громкое время: блок 125 мс — щелчок, два — слово
    calls = []
    class Whisper:
        def transcribe(self, audio, **kw):
            calls.append(1)
            return [], None
    for loud, want in ((0.125, 0), (0.25, 1)):
        calls.clear()
        d = types.SimpleNamespace(state=V.SHELL, lang="ru", whisper=Whisper(), noise_gate=100,
                              ears=types.SimpleNamespace(heard=set(), now=lambda: 0.0),
                                  MIN_SHELL_S=V.Daemon.MIN_SHELL_S, MIN_LOUD_S=V.Daemon.MIN_LOUD_S,
                                  silence=0.0, speech=loud,
                                  buf=[V.np.ones(2 * V.RATE, dtype=V.np.int16)])
        with mock.patch.object(V, "log"):
            V.Daemon.flush_dictation(d)
        check(f"громко {loud} с — whisper вызван {want} раз", len(calls), want)


def test_llm_unload_after_shell():
    print("── LLM выгружается через минуту после КОНСОЛИ ──")
    # после КОНСОЛИ модель держала 3390 МБ ещё 30 минут (keep_alive), и
    # диктовка рядом с ней рисковала out of memory
    class LLM:
        unloads = 0
        def unload(self):
            self.unloads += 1
            return True
    class Timer:
        made = []
        def __init__(self, delay, fn):
            self.delay, self.fn, self.cancelled = delay, fn, False
            Timer.made.append(self)
        def start(self): pass
        def cancel(self): self.cancelled = True
    stub = lambda *a, **k: None
    d = types.SimpleNamespace(
        state=V.SHELL, llm=LLM(), _llm_timer=None, lang="ru", LLM_IDLE_S=V.Daemon.LLM_IDLE_S,
        mouse=types.SimpleNamespace(stop=stub), head=types.SimpleNamespace(off=stub),
        inp=types.SimpleNamespace(release_all=stub), ears=types.SimpleNamespace(listen=stub))
    d._unload_idle_llm = lambda: V.Daemon._unload_idle_llm(d)
    with mock.patch.object(V.threading, "Timer", Timer), mock.patch.object(V, "log"), \
            mock.patch.object(V, "feedback"):
        V.Daemon.set_state(d, V.COMMAND)
        check("выход из КОНСОЛИ — таймер на минуту",
              [t.delay for t in Timer.made], [V.Daemon.LLM_IDLE_S])
        check("минута — это 60 с", V.Daemon.LLM_IDLE_S, 60.0)
        Timer.made[-1].fn()
        check("через минуту выгружена", d.llm.unloads, 1)
        # вернулись раньше минуты — таймер отменён, модель остаётся
        V.Daemon.set_state(d, V.SHELL)
        V.Daemon.set_state(d, V.DICTATION)
        V.Daemon.set_state(d, V.SHELL)
        check("вернулись в КОНСОЛЬ — таймер отменён", Timer.made[-1].cancelled, True)
        # таймер успел сработать, когда уже снова КОНСОЛЬ — не выгружаем
        Timer.made[-1].fn()
        check("снова КОНСОЛЬ — не выгружаем", d.llm.unloads, 1)
        # между режимами без КОНСОЛИ таймеров не заводим
        n = len(Timer.made)
        V.Daemon.set_state(d, V.COMMAND)
        V.Daemon.set_state(d, V.DICTATION)
        check("КОМАНДЫ -> ДИКТОВКА — без таймера", len(Timer.made), n + 1)


def test_fail_survives():
    print("── сбой не роняет демона ──")
    class Stop(BaseException):
        pass
    class Loop:
        """Демон без звука: блоки из списка, а step падает, как попросят."""
        state, lang = V.COMMAND, "ru"
        _last_fail, _last_fail_t = None, 0.0
        MIC_TIMEOUT_S = V.Daemon.MIC_TIMEOUT_S
        def __init__(self, blocks, mute=False):
            self.blocks, self.done, self.mute = list(blocks), [], mute
            self.q = self
        def get(self, timeout=None):
            if not self.blocks:
                # mute — звук кончился: Queue.get так и сообщает о таймауте
                raise V.queue.Empty if self.mute and timeout else Stop
            return self.blocks.pop(0)
        def on_audio(self, *a):
            pass
        def step(self, data):
            if data == "плохо":
                raise RuntimeError("нет CUDA")
            self.done.append(data)
        def fail(self, ex):
            V.Daemon.fail(self, ex)
    class Stream:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
    logged, sounds = [], []
    real = (V.sd.query_devices, V.sd.RawInputStream, V.feedback, V.play, V.log)
    V.sd.query_devices = lambda kind: {"name": "тест"}
    V.sd.RawInputStream = Stream
    V.feedback = lambda *a, **kw: None
    V.play = sounds.append
    V.log = lambda *a: logged.append(" ".join(map(str, a)))
    try:
        d = Loop(["раз", "плохо", "плохо", "два"])
        try:
            V.Daemon.run(d)
        except Stop:
            pass
        check("после сбоя блоки идут дальше", d.done, ["раз", "два"])
        check("одинаковый сбой подряд — один раз",
              (sum("СБОЙ" in l for l in logged), sounds), (1, ["dialog-error"]))
        # Звук пропал: не ждать вечно, а выйти — службу поднимет systemd.
        d = Loop(["раз"], mute=True)
        try:
            V.Daemon.run(d)
            lost = False
        except V.MicLost:
            lost = True
        check("нет звука — MicLost, а не вечное ожидание", (d.done, lost), (["раз"], True))
        # «печатать», а whisper не грузится: режим остаётся прежним
        d = Loop([])
        def no_cuda():
            raise RuntimeError("CUDA driver version is insufficient")
        d._ensure_whisper = no_cuda
        d.set_state = lambda new, note="": setattr(d, "state", new)
        try:
            V.Daemon.run_command(d, ("dictate", "ru"))
        except RuntimeError as ex:
            V.Daemon.fail(d, ex)
        check("whisper не загрузился — режим КОМАНДЫ", d.state, V.COMMAND)
    finally:
        V.sd.query_devices, V.sd.RawInputStream, V.feedback, V.play, V.log = real


def test_worker():
    print("── разбор КОНСОЛИ в своём потоке ──")
    w = V.Worker("тест")
    gate = V.threading.Event()
    first = w.submit(gate.wait)
    dropped = w.submit(lambda: "не должна идти")
    bad = w.submit(lambda: 1 / 0)
    last = w.submit(str.upper, "ls")
    dropped.cancel()
    gate.set()
    check("по порядку, отменённая не идёт",
          (first.result(2), last.result(2), dropped.cancelled(),
           type(bad.exception(2)).__name__),
          (True, "LS", True, "ZeroDivisionError"))
    # Идущий запрос к LLM не держит выход: на ThreadPoolExecutor — 3 с
    # ожидания поверх импорта.
    code = ("import sys, time; sys.path.insert(0, %r); import voiced; "
            "voiced.Worker('тест').submit(time.sleep, 3); time.sleep(0.1); "
            "t = time.time(); sys.stdout.write(str(t)); sys.stdout.flush(); sys.exit(0)"
            % os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    waited = time.time() - float(out.stdout)
    check(f"выход не ждёт задачу ({waited:.2f} с)", waited < 1.0, True)


def main():
    d = home()
    test_rules(d)
    test_paths(d)
    test_user()
    test_home_dirs()
    test_runnable()
    test_closest()
    test_complete()
    test_fit()
    test_llm(d)
    test_chatter(d)
    test_config()
    test_cwd()
    test_quoting()
    test_run_phrase()
    test_whisper_run()
    test_shell_idle()
    test_prompt_echo()
    test_ollama_warm()
    test_llm_after_whisper()
    test_undo()
    test_exit_tail()
    test_exit_echo()
    test_glue_pastes()
    test_shell_no_fallback()
    test_click_is_not_speech()
    test_llm_unload_after_shell()
    test_fail_survives()
    test_worker()
    print("\nИТОГ:", "ПРОЙДЕН" if not FAILS else f"ПРОВАЛЕН: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
