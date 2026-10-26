"""Режим КОНСОЛЬ: сказанная фраза -> команда шелла.

Сначала правила: произносимые символы, буквы флагов, имена программ и пути,
сверенные с настоящей файловой системой. Если первое слово результата — не
программа и не встроенная команда оболочки, фраза, скорее всего, сказана
намерением («покажи файлы в загрузках»), и её переводит LLM.

Модуль не знает о демоне, микрофоне и whisper: на входе текст и каталог,
на выходе строка команды. Поэтому он проверяется без звука и без сети.
"""

import difflib, getpass, glob, json, os, re, shlex, shutil, subprocess, tomllib, traceback, urllib.request

import complete

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".config", "nohands", "config.toml")
DEFAULTS = {"backend": "ollama", "model": "qwen2.5-coder:7b",
            "url": "http://localhost:11434"}

# Подсказка whisper: слова из неё он охотнее пишет так же, кириллицей, —
# а правила ждут именно кириллицу.
WHISPER_PROMPT = ("гит статус, сиди тильда слэш загрузки, эл эс минус эл эй, "
                  "кэт точка тэ икс тэ, судо эпт, два минуса хелп, пайп греп, эм си")



def prompt_echo(text):
    """Текст — это подсказка, которую whisper повторил на тишине?

    На почти тихом фрагменте он выдаёт initial_prompt дословно или без хвоста.
    Настоящая команда может совпасть с кусочком подсказки («эл эс минус эл
    эй»), поэтому сравниваем с подсказкой целиком: команда много короче её.
    """
    a, b = " ".join(norm(text)), " ".join(norm(WHISPER_PROMPT))
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
        return True
    # Хвост подсказки из нескольких её пунктов: «кэт тэ, судо эпт, два минуса
    # хелп, эм си» (журнал 21:07). Одна команда пунктов подсказки не склеивает.
    items = [" ".join(norm(i)) for i in WHISPER_PROMPT.split(",")]
    hits = [i for i in items if i and i in a]
    return len(hits) >= 2 and sum(map(len, hits)) >= 0.6 * len(a)


# Ключи нормализованы как в norm(): нижний регистр, «ё» -> «е».
SYMBOLS = {
    "два минуса": "--", "двойной минус": "--",
    "слэш": "/", "слеш": "/", "точка": ".", "тильда": "~",
    "минус": "-", "дефис": "-", "подчеркивание": "_", "равно": "=",
    "двоеточие": ":", "доллар": "$", "кавычка": '"',
    "пайп": "|", "труба": "|", "больше": ">", "меньше": "<",
    "амперсанд": "&", "звездочка": "*", "собака": "@",
    "плюс": "+", "запятая": ",", "точка с запятой": ";", "процент": "%",
    "решетка": "#", "восклицательный знак": "!", "вопросительный знак": "?",
    "одинарная кавычка": "'", "апостроф": "'", "обратный слэш": "\\",
    "обратный слеш": "\\", "бэкслэш": "\\",
}
# Скобки: «открыть скобку» / «открывающая скобка», и то же с «квадратная»,
# «фигурная»; квадратная и фигурная без «открыть» — тоже открывающая.
for _o, _c, _nom, _acc in [("(", ")", "", ""), ("[", "]", "квадратная ", "квадратную "),
                           ("{", "}", "фигурная ", "фигурную ")]:
    SYMBOLS.update({f"открыть {_acc}скобку": _o, f"открывающая {_nom}скобка": _o,
                    f"закрыть {_acc}скобку": _c, f"закрывающая {_nom}скобка": _c})
    if _nom:
        SYMBOLS[f"{_nom}скобка"] = _o
OPEN, CLOSE = "([{", ")]}"
# Как символ стыкуется с соседями: BOTH — с обеими сторонами (пути, имена
# файлов), RIGHT — только со следующим (флаги, переменные), остальные —
# операторы, они стоят отдельно.
GLUE_BOTH = {"/", ".", "_", "=", ":", "*", "@", ",", "%", "?", "\\", "!"}
GLUE_RIGHT = {"-", "--", "~", "$", '"', "+", "'"}

LETTERS = {
    "эй": "a", "а": "a", "би": "b", "си": "c", "цэ": "c", "це": "c", "ди": "d", "эф": "f", "джи": "g",
    "эйч": "h", "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эль": "l", "эм": "m",
    "эн": "n", "оу": "o", "пи": "p", "кью": "q", "эр": "r", "эс": "s",
    "ти": "t", "тэ": "t", "ю": "u", "ви": "v", "дабл ю": "w", "икс": "x",
    "уай": "y", "зед": "z",
}
# Исключения: как произносят -> как пишут — только то, до чего подбор по
# установленным программам и completion (_fit) не дотягивается или находит
# не то. Запись оставлена, если без неё результат меняется хотя бы в одном
# контексте, где её ждут: сама по себе, с числом («кил девять»), с русским
# словом («пуш файлы») или после программы, но только там, где completion
# этой программы предлагает значение (подкоманда у git, docker; ключ у ls).
# Классы: транслит не знает английского произношения («тач» -> tach ближе к
# tac, 0.86, чем к touch; «хед» — head с отрывом 0.06 от hd; «питон», «клод»,
# «ходе» — 0.67–0.75 с чужими программами); трёхбуквенным программам
# нечёткий выбор закрыт (нужно ≥4 букв: «кил», «кэт», «вим», «эхо»);
# подкоманды без программы («пуш», «лог», «стоп», «статус»); нечётко
# подобранная программа при русских словах в команде возвращается как
# сказано («докер композ ап», «докер контейнер прюн»); «ай пи» без записи
# слипается в ipa; «хелп» — ключ, которого нет в completion git commit,
# docker, python3, ping (без записи — --хелп, замер 05.10). Остальное
# подбирается само (замер 04.10).
WORDS = {
    # файлы и каталоги
    "элэс": "ls", "сиди": "cd",
    # так cd тоже говорят, по журналу; без записи фраза уходила в LLM, и та
    # выдумывала cd ~/Картинки и sed вместо cd
    "цд": "cd", "цэ дэ": "cd", "цэдэ": "cd", "сэдэ": "cd", "сэ дэ": "cd",
    "кэт": "cat", "кет": "cat", "кат": "cat",
    "мор": "more", "мау": "more", "моар": "more",
    "лэс": "less", "лис": "less", "лез": "less",
    "хед": "head", "тейл": "tail", "найти": "find", "файнд": "find",

    "тач": "touch", "чоун": "chown", "эхо": "echo",
    "экзит": "exit",
    "анзип": "unzip",
    "ган зип": "gunzip", "анрар": "unrar",
    # редакторы
    "вим": "vim", "эм си эдит": "mcedit", "эмси эдит": "mcedit",
    "эм цэ эдит": "mcedit", "эм це эдит": "mcedit", "эмцэ эдит": "mcedit",
    "эмсиэдит": "mcedit",
    "мс": "mc", "эмси": "mc", "эмцэ": "mc", "эмце": "mc",       # так whisper пишет «эм си» (журнал 21:01)
    # система
    "эпт": "apt", "кил": "kill",
    "эйчтоп": "htop",
    "аж топ": "htop", "и чтоп": "htop", "ичтоп": "htop",
    "системктл": "systemctl", "систем си ти эл": "systemctl",
    "скрин": "screen",
    # сеть
    "эс си пи": "scp", "эсэсэйч": "ssh", "курл": "curl",
    "керл": "curl", "эн эс лукап": "nslookup",
    "энэс лукап": "nslookup", "хуиз": "whois", "трейсрут": "traceroute",
    "трейс роут": "traceroute", "резолв си ти эл": "resolvectl",
    "резолвктл": "resolvectl", "резолвконтрол": "resolvectl",
    "опен эс эс эл": "openssl", "оупен эс эс эл": "openssl",
    "вэ гэ квик": "wg-quick", "дабл ю джи квик": "wg-quick", "вэ гэ": "wg",
    "ай пи": "ip",      # словом: «ай пи эй» -> ip a, не ipa
    # разработка
    "питон": "python3", "пайтест": "pytest", "пай тест": "pytest",
    "гоу": "go",
    "клод": "claude", "клоуд": "claude", "клоут": "claude", "клауд": "claude",
    "клауде": "claude", "клауди": "claude", "клауда": "claude", "клада": "claude",
    "клад": "claude",
    "код": "code", "коуд": "code", "ходе": "code",     # «ходе» — так пишет whisper
    "докер": "docker", "доккер": "docker", "дукер": "docker",
    "куб си ти эл": "kubectl", "кубконтрол": "kubectl",
    # подкоманды git, docker, terraform, kubectl
    "статус": "status", "эд": "add", "коммит": "commit", "пуш": "push",
    "пул": "pull", "лог": "log", "чекаут": "checkout",
    "клон": "clone", "ремоут": "remote", "стэш": "stash", "стеш": "stash",
    "тэг": "tag", "тег": "tag", "дескрайб": "describe", "конфиг": "config",
    "ран": "run", "билд": "build", "композ": "compose", "имиджес": "images",
    "стоп": "stop", "план": "plan", "эплай": "apply", "аутпут": "output",
    "гет": "get", "квери": "query", "роут": "route", "апдейт": "update",
    "ап": "up", "даун": "down", "пэс": "ps",
    "контейнер": "container", "прюн": "prune", "прун": "prune", "прум": "prune",
    # флаги словами: «два минуса хелп»
    "хелп": "help", "резюм": "resume", "нейм": "name",
    "таргет": "target",
    # all слышится как «ол»: по звуку ol против al — 0.5, а склеить a с o в
    # _sound — clone проигрывает соперникам (сеанс 05.10)
    "ол": "all", "олл": "all",
    # пользователь: «эс эс эйч рут собака …» -> ssh root@…
    "рут": "root",
}
# принудительный разрыв: «гит эд пробел точка» -> git add .; «пробил» —
# так его пишет whisper, по журналу
SPACES = ("пробел", "пробил")


def leading_space(text):
    """Фраза начинается с «пробел». Правила его съедают — слева нет токена,
    от которого отделять, — а сказан он как раз ради пробела перед вставкой."""
    return norm(text)[:1] in [[w] for w in SPACES]
TABLE = {**{k: ("sym", v) for k, v in SYMBOLS.items()},
         **{k: ("letter", v) for k, v in LETTERS.items()},
         **{k: ("word", v) for k, v in WORDS.items()},
         **{k: ("space", "") for k in SPACES}}
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

SHELLS = {"bash", "zsh", "fish", "sh", "dash"}

TRANSLIT = [("дж", "j"), ("кс", "x"), ("а", "a"), ("б", "b"), ("в", "v"),
            ("г", "g"), ("д", "d"), ("е", "e"), ("ж", "zh"), ("з", "z"),
            ("и", "i"), ("й", "y"), ("к", "k"), ("л", "l"), ("м", "m"),
            ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"), ("с", "s"),
            ("т", "t"), ("у", "u"), ("ф", "f"), ("х", "h"), ("ц", "ts"),
            ("ч", "ch"), ("ш", "sh"), ("щ", "sch"), ("ъ", ""), ("ы", "y"),
            ("ь", ""), ("э", "e"), ("ю", "yu"), ("я", "ya")]


# Английские окончания, которые транслит по буквам не узнаёт: «фьюзжен» ->
# fyuzzhen, похоже на fusion на 0.43 при пороге 0.7; с заменой — 0.92.
# «ктл» — ctl у systemctl, resolvectl, kubectl: «системктл» похож на
# systemctl на 0.78, с заменой — на 0.89; «кубектл» — 1.00 вместо 0.86.
SOUNDS = [("зжен", "жен"), ("жен", "sion"), ("жн", "sion"), ("шен", "tion"),
          ("шн", "tion"), ("ктл", "ctl")]


def english(s):
    for ru, en in SOUNDS:
        s = s.replace(ru, en)
    # «апгрейд» по буквам — apgreyd, 0.57 с upgrade при пороге 0.6: «ап» в
    # начале — это up, «ейд» на конце — ade с немой e (так же rename, update)
    s = re.sub(r"^ап", "up", s)
    s = re.sub(r"ей([бвгдзклмнпрстф])$", r"a\1e", s)
    return translit(s)


def translit(s, ks=False):
    """ks — «кс» по буквам: «докс» -> doks, а не dox (иначе до docs не достать)."""
    for ru, en in TRANSLIT:
        if not (ks and ru == "кс"):
            s = s.replace(ru, en)
    return s


def norm(text, keep_case=False):
    """Текст whisper -> слова. Знаки, которые whisper пишет сам, сохраняем."""
    s = text.replace("ё", "е").replace("Ё", "Е").strip()
    s = s if keep_case else s.lower()
    s = re.sub(r"[,!?…«»]", " ", s)
    if s.endswith(".") and not s.endswith(".."):
        s = s[:-1]          # точка в конце предложения, а не в команде
    return s.split()


def _words(raw):
    """Слова для правил. Латиница, которую whisper написал с заглавных,
    остаётся как есть: «эхо доллар HOME» — это $HOME, а не $home. Кроме
    первого слова, если оно не целиком заглавное, — это начало предложения
    («Git status»). Имена файлов регистр всё равно сверят с диском."""
    out = []
    for k, w in enumerate(norm(raw, keep_case=True)):
        latin = re.search(r"[A-Z]", w) and not re.search(r"[А-Яа-я]", w)
        out.append(w if latin and (k or w.isupper()) else w.lower())
    return out


# Числа whisper в консоли пишет прописью: «сто девяносто», и LLM на них
# отвечала «НЕТ». Разряд — чтобы «сто девяносто два» было 192, а не 100 90 2.
NUMBER_WORDS = {}
for _k, _ws in enumerate([
        "ноль", "один одна одно", "два две", "три", "четыре", "пять", "шесть",
        "семь", "восемь", "девять", "десять", "одиннадцать", "двенадцать",
        "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать",
        "восемнадцать", "девятнадцать"]):
    NUMBER_WORDS.update({w: (_k, 1 if _k < 10 else 2) for w in _ws.split()})
for _k, _ws in enumerate(["двадцать", "тридцать", "сорок сорока", "пятьдесят",
                          "шестьдесят", "семьдесят", "восемьдесят",
                          "девяносто девяноста"], 2):
    NUMBER_WORDS.update({w: (_k * 10, 2) for w in _ws.split()})
for _k, _ws in enumerate(["сто ста", "двести", "триста", "четыреста", "пятьсот",
                          "шестьсот", "семьсот", "восемьсот", "девятьсот"], 1):
    NUMBER_WORDS.update({w: (_k * 100, 3) for w in _ws.split()})
THOUSAND = {"тысяча", "тысячи", "тысяч"}


def _number(words, i):
    """Числительные с позиции i -> (цифры, сколько слов) или None.

    Идущие подряд числа склеиваются: «восемь ноль восемь ноль» — это порт
    8080, а не четыре аргумента."""
    digits, j = "", i
    while j < len(words):
        value, rank, start = 0, 4, j        # rank — младший занятый разряд
        while j < len(words):
            w = words[j].lower()
            if w in THOUSAND and value < 1000 and j > start:
                value, rank = value * 1000, 4
            elif w in NUMBER_WORDS and NUMBER_WORDS[w][1] < rank:
                # после десятка — только единицы: «двадцать шесть», не «двадцать пятнадцать»
                v, r = NUMBER_WORDS[w]
                value += v
                rank = 1 if r == 2 and v % 10 else r
            else:
                break
            j += 1
        if j == start:
            break
        digits += str(value)
    return (digits, j - i) if digits else None


def current_user():
    return getpass.getuser()


def _user(word):
    """Слово, похожее на имя текущего пользователя, -> это имя. Имя не
    зашито: берём у системы. «штрейбер» — транслит shtreyber, похоже на
    streiber только на 0.82, поэтому ещё и немецкое чтение: шт -> st,
    ей -> ei. Порог выше, чем для файлов: «стример» похож на streiber на 0.80."""
    if not re.fullmatch(r"[а-я]+", word):
        return None
    name = current_user()
    german = translit(word.replace("шт", "ст").replace("ей", "еи")
                      .replace("ай", "аи"))
    for probe in (translit(word), german):
        if difflib.SequenceMatcher(None, probe, name.lower()).ratio() >= 0.85:
            return name
    return None


def _lex(words):
    """Слова -> (вид, текст) по самому длинному совпадению с таблицей."""
    out, i = [], 0
    while i < len(words):
        num = _number(words, i)
        for n in range(min(LONGEST, len(words) - i), 0, -1):
            key = " ".join(words[i:i + n]).lower()
            if key in TABLE and not (num and num[1] > n):    # «два минуса» — флаг
                out.append(TABLE[key])
                i += n
                break
        else:
            if num:
                out.append(("word", num[0]))
                i += num[1]
                continue
            w = words[i]
            out.append(("sym", w) if w in SYMBOLS.values() else ("word", _user(w) or w))
            i += 1
    return out


def _glue(items):
    """(вид, текст) -> токены командной строки."""
    toks, pending, last, quote = [], False, None, None
    for kind, text in items:
        if kind == "space":
            pending, last = False, "space"
            continue
        if kind == "sym" and text in OPEN:
            # к $ липнет: «доллар открыть скобку» — подстановка $(…)
            join = bool(toks) and toks[-1].endswith("$")
            pending, kind = True, "path"
        elif kind == "sym" and text in CLOSE:
            join, pending, kind = True, False, "path"
        elif kind == "sym" and text == quote:
            # вторая кавычка пары закрывает — липнет к слову слева, а не к
            # следующему: иначе выходило "фикс "
            join, pending, quote = True, False, None
        elif kind == "sym" and text in GLUE_BOTH:
            # к имени программы не липнем: «сиди точка точка» — это cd ..
            # «*» к флагу тоже: «эр эм минус эр эф звёздочка» — rm -rf *
            join = (bool(toks) and (len(toks) > 1 or not runnable(toks[0]))
                    and last not in ("op", "space")
                    and not (text == "*" and toks[-1].startswith("-")))
            join = join or pending
            pending, kind = True, "path"
        elif kind == "sym" and text in GLUE_RIGHT:
            # «минус минус ол» — так whisper пишет «два минуса»: --ол, а не
            # два токена - -ол (сеанс 05.10)
            join = (pending and last == "path") or (
                text == "-" and last == "sym" and bool(toks) and toks[-1] == "-")
            pending = True
            if text in "\"'":
                quote = text
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
    # число — не имя: нечёткий поиск нашёл бы 8081.txt на месте 8080
    if re.fullmatch(r"[\d.:,]+", arg):
        return arg
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
    # сказанный в конце «слэш» — это «дальше внутрь», для Tab: без него
    # «слэш» давал '' (пустое имя в кавычках), «тильда слэш» — ~
    if arg.endswith("/") and not path.endswith("/"):
        path += "/"
    return path


def _walk(toks, cwd, file_ok=False):
    """Слова после cd -> путь из существующих каталогов или None. С file_ok
    последним может быть и файл: «точка слэш сервер билдер» -> ./server-builder.

    На каждом уровне пробуем все группы слов, которые склеиваются в имя
    каталога, и из разборов до конца берём самый похожий. Первая подходящая
    самая длинная группа не годится: «атлас медиа фьюжн» похоже на atlasmedia
    на 0.77, и fusion пропадал; по частям — atlasmedia 1.0 и fusion 0.92.
    При равном сходстве выигрывает группа длиннее.
    Через сказанный «слэш» группа не переходит: atlasmediafusion похоже на
    atlasmedia на 0.77, и fusion пропадал."""
    if any(t.startswith("-") for t in toks[1:]):     # флаг — уже не имя
        return None
    first = toks[0]
    if first.startswith("~"):
        base, prefix, toks = HOME, "~/", [first[1:]] + toks[1:]
    elif first.startswith("/"):
        base, prefix = "/", "/"
    else:
        base, prefix = cwd, ""
    words, cut = [], set()          # cut — перед этим словом был слэш
    for t in toks:
        for k, w in enumerate(t.split("/")):
            if k and words:
                cut.add(len(words))
            if w:
                words.append(w)
    if not all(re.fullmatch(r"[\w.\u00a0-]+", w) for w in words):
        return None

    def down(base, i):
        """(части пути, сходство, взвешенное по длине сказанного) или None."""
        if i == len(words):
            return [], 0.0
        stop = min([c for c in cut if c > i] + [len(words)])
        best = None
        for n in range(stop - i, 0, -1):
            said = "".join(words[i:i + n])
            hit, ratio = (said, 1.0) if said in (".", "..") else _match_ratio(said, base)
            if hit is None and file_ok and i + n == len(words):
                hit, ratio = _match_script(said, base)
            if hit is None and n == stop - i:
                hit, ratio = _match_dir(said, base)
            full = os.path.join(base, hit) if hit is not None else ""
            last = file_ok and i + n == len(words) and os.path.exists(full)
            if hit is not None and (os.path.isdir(full) or last):
                rest = down(full, i + n)
                if rest is not None:
                    score = ratio * len(said) + rest[1]
                    if best is None or score > best[1]:
                        best = [hit] + rest[0], score
        return best

    got = down(base, 0)
    if got is None:
        return None
    path = (prefix + "/".join(got[0])) or prefix.rstrip("/") or "."
    # конечный слэш — только сказанный: «точка слэш» -> ./ (дальше Tab или
    # имя), а «сиди тильда» -> ~, не ~/
    if toks[-1].endswith("/"):
        return path if path.endswith("/") else path + "/"
    return path.rstrip("/") or path


def _match(part, base):
    return _match_ratio(part, base)[0]


def _match_ratio(part, base):
    """Имя в base, похожее на сказанное, и сходство: (имя, 0..1) или (None, 0)."""
    try:
        names = os.listdir(base)
    except OSError:
        return None, 0.0
    part = part.lower()
    said = part.replace(JOIN, " ")
    # Папка с тем же именем, что сказано, важнее синонима: ~/Downloads бывает
    # симлинком на настоящие ~/Загрузки.
    same = [n for n in names if n.lower() == said]
    if same:
        return same[0], 1.0
    syn = SYNONYMS.get(said)
    if syn in names:
        return syn, 1.0
    # дефисы и подчёркивания не произносят: «атлас сервер билдер» —
    # atlas-server-builder
    lower = {n.lower(): n for n in names}
    lower.update({re.sub(r"[-_]", "", k): n for k, n in list(lower.items())
                  if re.sub(r"[-_]", "", k) not in lower})
    for probe in (part, translit(part), translit(part, ks=True), english(part)):
        got = difflib.get_close_matches(probe, list(lower), n=1, cutoff=0.7)
        if got:
            return lower[got[0]], difflib.SequenceMatcher(None, probe, got[0]).ratio()
    return None, 0.0


def _match_script(said, base):
    """Файл, который запускают: «точка слэш крит ривер порт» ->
    create.riverport.sh. Расширение и точки не произносят, а whisper
    коверкает слова: с полным именем сходство 0.56–0.67 при пороге 0.7
    (журнал 28.09). Файлов в каталоге немного, поэтому порог ниже, но
    лучший должен заметно обгонять второй: там 0.63–0.75 против ≤0.54."""
    return _match_loose(said, base, os.path.isfile, 0.6, 0.1)


def _match_dir(said, base):
    """Каталог для cd, когда строгий порог 0.7 ничего не нашёл. whisper пишет
    английское имя русскими словами: «ин провинция селой» — in-province-cli
    на 0.58, «ин про винс клей» — на 0.69 (журнал 28.09, 14:52: три попытки
    подряд, 0.52–0.69). Второй каталог при этом не выше 0.42 — отрыв и
    отличает имя от случайной похожести."""
    return _match_loose(said, base, os.path.isdir, 0.5, 0.15)


def _match_loose(said, base, kind, cutoff, lead):
    """Лучшее имя нужного вида (файл, каталог) со сходством от cutoff, если
    оно обгоняет второе хотя бы на lead: (имя, сходство) или (None, 0)."""
    try:
        names = [n for n in os.listdir(base) if kind(os.path.join(base, n))]
    except OSError:
        return None, 0.0
    stem = (lambda n: os.path.splitext(n)[0]) if kind is os.path.isfile else (lambda n: n)
    return _closest(said, names, cutoff, lead, stem=stem)


def _closest(said, names, cutoff, lead, stem=lambda n: n, variants=False, spoken=False):
    """Имя из names, похожее на сказанное: (имя, сходство) или (None, 0).

    Совпало точно (после транслита, без «-_.») и такое имя одно — берём.
    Иначе лучшее не ниже cutoff и обгоняет следующее на lead. С variants
    следующее — не вариант лучшего: docker против dockerd — отрыв 0.08,
    против настоящего соперника done — 0.24 (замер 04.10). С spoken имя
    сравнивается ещё и с тем, как его произносят (_pronounce)."""
    bare = lambda s: re.sub(r"[-_.]", "", s.lower())
    probes = {bare(p) for p in (said, translit(said), translit(said, ks=True), english(said))}
    keys = [(bare(stem(n)), n) for n in names]
    exact = [n for k, n in keys if k in probes]
    if len(exact) == 1:
        return exact[0], 1.0
    # Нечётко сравниваем звучание, а не буквы: whisper пишет как слышит.
    # «комит» — komit против commit 0.73 по буквам при пороге 0.80, по звуку
    # 1.0; «дискрибе» — 0.75 и 0.88 (сеанс 05.10). Совпавшее по звуку — не
    # точное: «ты» звучит как tty, «в» — как w, а 1.0 у _program — «сказано
    # точно», и правило «от 4 букв» не сработало бы.
    sounds = {_sound(p) for p in probes}
    # Имя ниже cutoff - lead не помешает ни как лучшее, ни как соперник, а
    # quick_ratio — верхняя граница ratio и много дешевле: программ 3586.
    floor = cutoff - lead
    scored = []
    for k, n in keys:
        best = 0.0
        for sk in {_sound(k), _sound(_pronounce(k))} if spoken else {_sound(k)}:
            for p in sounds:
                m = difflib.SequenceMatcher(None, p, sk)
                if m.real_quick_ratio() >= floor and m.quick_ratio() >= floor:
                    best = max(best, min(m.ratio(), 0.99))
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


def _sound(s):
    """Латиница -> как звучит: c и q — k, x — ks, удвоенная буква — одна.
    Транслит сказанного пишет k и одну букву, английское имя — c и две.

    И как пишет whisper русскую речь: звонкая на конце оглушается
    («дискрайп»), безударное «е» — «и» («дискриби»). Без этого обоих нет у
    describe (0.75); «рисет» не доставал reset (сеанс 05.10)."""
    for a, b in (("ck", "k"), ("ph", "f"), ("qu", "kv"), ("x", "ks"), ("c", "k"),
                 ("q", "k"), ("w", "v")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return (s[:-1] + s[-1:].translate(DEVOICE)).replace("e", "i")


DEVOICE = str.maketrans("bdgvz", "ptkfs")


# Немая e на конце: describe говорят «дискрайб» — по написанию diskrayb
# против deskribe 0.62, по произношению deskrayb — 0.88 (сеанс 05.10).
# Только для подкоманд и ключей: среди 3586 программ так находятся русские
# слова — «файлы» звучит как file, и «покажи файлы» без первого слога
# становилось командой file.
MAGIC_E = {"a": "ey", "i": "ay", "o": "ou", "u": "u", "e": "i"}


def _pronounce(s):
    return re.sub(r"([aeiou])([^aeiouy])e$", lambda m: MAGIC_E[m[1]] + m[2], s)


def _merge_synonyms(words):
    """«рабочий стол» — два слова, но один каталог: склеиваем до разбора."""
    out, i = [], 0
    while i < len(words):
        for n in range(min(SYNONYM_LONGEST, len(words) - i), 1, -1):
            key = " ".join(words[i:i + n]).lower()
            if key in SYNONYMS:
                out.append(key.replace(" ", JOIN))
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


# (порог, отрыв) нечёткого выбора; замер 04.10. Программ 3586: нужные —
# docker 0.91, pytest 0.92, kubectl 0.86, systemctl 0.89 с отрывом от 0.15.
# Слова намерения порог не отсекает: «проверь» -> prove 0.91. Просьбу
# словами берегут другие правила: нечётко — только кириллица от 4 букв, а
# подобранное имя возвращается как сказано, если в команде остались
# русские слова (нечёткое — хоть одно; точный транслит — от двух, и все
# аргументы русские), см. _fit. Что выбор находит не то («тач» -> tac 0.86
# с отрывом 0.11) или не находит («хед» — отрыв 0.06), — исключения в
# WORDS: они проверяются раньше.
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
    """Имя программы из 1–3 сказанных слов: (имя, сколько слов взято,
    точно ли). «пай тест» — pytest. Точное имя из одного слова берётся сразу;
    иначе лучшее по сходству, при равном — из большего числа слов. Не
    нашлось — (как сказано, 1, True): дальше решает LLM.
    Нечётко — только кириллица, как в _word: латиницу уже выбрали WORDS или
    whisper, и рядом с ней почти всегда есть другая программа — push ->
    pushd, stop -> top, clone -> rclone."""
    t = toks[i]
    names = complete.programs()
    if t in names or not NAME.fullmatch(t):
        return t, 1, True
    found = []
    for n in (1, 2, 3):
        group = toks[i:i + n]
        if len(group) < n or not all(NAME.fullmatch(w) for w in group):
            break
        said = "".join(group)
        hit, ratio = _closest(said, names, *PROGRAM_FIT, variants=True)
        if hit and ratio == 1.0 and n == 1:
            return hit, 1, True
        # Короткому слову похожая программа найдётся почти всегда: «там» —
        # tasm 0.86, «тут» — tput 0.86, «он» — pon 0.80, «ты» — tty 0.80.
        # Точное совпадение («нет» — net) от длины не зависит.
        cyrillic = all(re.fullmatch(r"[а-я]+", w) for w in group)
        if hit and (ratio == 1.0 or (cyrillic and len(said) >= 4)):
            found.append((ratio, n, hit))
    if not found:
        return t, 1, True
    ratio, n, hit = max(found)
    return hit, n, ratio == 1.0


def _word(t, cands):
    """Сказанное слово -> кандидат позиции (подкоманда, служба, хост) или None.
    Нечётко — только кириллица: латиницу уже выбрали правила — по буквам
    («эф ай икс» — новая ветка fix, а не соседняя fix2, 0.86) или по имени
    пользователя (_user: brenner ушёл бы в соседнего brenn, 0.83)."""
    if t in cands:
        return t
    return _closest(t, cands, *WORD_FIT, spoken=True)[0] if re.fullmatch(r"[а-я]+", t) else None


def _option(t, cmd):
    """«--хелп» -> --help: длинный ключ программы, похожий на сказанное.
    Одним минусом тоже: «минус вербоус» — это --verbose, а не -в…"""
    m = re.fullmatch(r"-{1,2}([а-я][а-я-]*)", t)
    if not m or not cmd:
        return t
    # «--region=» у harborctl, «--help-all:» — так справку python3 разбирает
    # bash-completion
    longs = {o.rstrip("=:")[2:]: o.rstrip("=:") for o in complete.options(cmd)
             if o.startswith("--")}
    hit = _closest(m.group(1), list(longs), *WORD_FIT, spoken=True)[0]
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
    # (где в out, как сказано, нечётко ли) — имя не подтверждено: подобрано
    # нечётко или по транслиту русского слова
    loose = None

    def settle():
        # Нечёткое имя и кириллица в аргументах — скорее просьба словами:
        # «проверь место на диске» — prove 0.91, но «место на диске» ни к
        # чему у prove не подобралось. Имя — как сказано, и фраза уходит в
        # LLM. Точный транслит — то же, если за ним два русских слова и
        # больше и ни одно не подобралось: «нет я не знаю» — net, «тест
        # прошёл нормально» — test. Остаются git commit -m фикс (аргумент
        # подобран), mkdir папка (один); echo привет — латиница из WORDS.
        if not loose:
            return
        at, said, fuzzy = loose
        rest = [bool(re.search(r"[а-я]", w, re.I)) for w in out[at + 1:]]
        if any(rest) if fuzzy else len(rest) >= 2 and all(rest):
            out[at] = said

    while i < len(toks):
        t = toks[i]
        if t in SEPARATORS:
            settle()
            out.append(t)
            cmd, prog, loose, i = [], 0, None, i + 1
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
            if not cmd:
                kind, cands = "program", []
            elif "/" not in cmd[prog] and cmd[prog] not in complete.programs():
                # первое слово — не программа («покажи последние три файла»):
                # Tab предложил бы файлы, и bash на каждое слово не нужен —
                # 9 запусков, 0.30 с на эту фразу (замер 05.10)
                kind, cands = "files", []
            else:
                kind, cands = complete.position(cmd, cwd) or ("files", [])
            hit = _word(t, cands) if kind == "words" else None
            if kind == "program":
                prog = len(cmd)
                got, n, exact = _program(toks, i)
                said = " ".join(toks[i:i + n])
                translit_only = exact and re.search(r"[а-я]", said, re.I)
                loose = (None if exact and not translit_only
                         else (len(out), said, not exact))
            elif kind == "dirs":
                got, n = _fit_path(toks, i, cwd)
            elif hit:
                got, n = hit, 1
            else:
                got, n = _resolve(t, cwd), 1
        out.append(_quote(got, cwd))
        cmd.append(got.replace(JOIN, " "))
        i += n
    settle()
    return out


SAFE = re.compile(r"[\w@%+=:,./~*-]+")


def _quote(t, cwd):
    """Кавычки — имени с пробелом или настоящему имени со спецсимволами.

    Тильда остаётся снаружи: в кавычках шелл её не раскрывает. Слово, которого
    нет на диске, со спецсимволами не трогаем: «$path» — это переменная.
    """
    t = t.replace(JOIN, " ")
    if t.startswith("~/"):
        return "~/" + (_quote(t[2:], HOME) if t[2:] else "")
    on_disk = os.path.lexists(os.path.join(cwd, t))
    if " " in t or (on_disk and not SAFE.fullmatch(t)):
        return shlex.quote(t)
    return t


# Частые переменные. Окружение демона — не окружение оболочки в терминале:
# EDITOR там может не быть, и «эдитор» уехал бы в GIT_EDITOR.
ENV_NAMES = ("HOME", "PATH", "USER", "SHELL", "PWD", "OLDPWD", "LANG", "TERM",
             "DISPLAY", "EDITOR", "HOSTNAME")


def _var(tok):
    """«$юзер» -> $USER. Имя переменной whisper пишет кириллицей (проверено
    на синтезе: «эхо доллар юзер» -> «эхо доллар юзер»), а не HOME/PATH."""
    name = tok[1:]
    # «$(дэйт» — подстановка, а не имя переменной
    if not re.search(r"[а-я]", name) or not re.fullmatch(r"\w+", name):
        return tok
    lat = translit(name).upper()
    got = (difflib.get_close_matches(lat, ENV_NAMES, n=1, cutoff=0.5)
           or difflib.get_close_matches(lat, list(os.environ), n=1, cutoff=0.75))
    return "$" + (got[0] if got else lat)


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


def runnable(cmd):
    first = cmd.split()[0] if cmd.split() else ""
    return bool(first) and (first in complete.programs() or shutil.which(first) is not None)


# ─────────────────────────────── LLM ─────────────────────────────────────────
# Прогретая модель отвечает за 0.35 с, а холодная загрузка в видеопамять
# занимает ~27 с — дольше таймаута. По умолчанию ollama выгружает модель через
# 5 минут простоя, и первая фраза после перерыва всегда уходила бы в таймаут.
KEEP_ALIVE = "30m"


def vram_free_mb():
    """Свободная видеопамять NVIDIA, МБ; None — если карты или nvidia-smi нет."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class Ollama:
    WARM_TIMEOUT = 120.0
    # Сколько слоёв влезет — по свободной видеопамяти перед загрузкой, когда
    # whisper уже в памяти. По журналу ollama: 16 слоёв 7b — 2834 МБ, 20 —
    # 3390 МБ, то есть ~140 МБ на слой и ~600 МБ сверх слоёв (кэш, буферы).
    # Запас: рабочие буферы whisper появляются лишь на первой фразе (+240 МБ
    # на фразе 8 с), и ~300 МБ рабочему столу и браузеру. 27.09 без замера 20
    # слоёв влезли впритык, и каждая фраза whisper падала с CUDA out of
    # memory: демон перестал слышать. С запасом 900 МБ свободным оставался
    # почти гигабайт — слоёв можно было взять на три больше.
    LAYER_MB, BASE_MB, RESERVE_MB = 140, 600, 500

    def __init__(self, model, url, timeout=10.0, options=None):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout
        # num_gpu — слоёв на видеокарте. Прогрев шлёт те же опции: с другим
        # числом слоёв ollama загрузила бы модель заново на первой фразе.
        self.options = dict(options or {})
        self.max_gpu = self.options.get("num_gpu")    # из config.toml — потолок

    def _post(self, body, timeout):
        req = urllib.request.Request(self.url + "/api/generate",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def complete(self, prompt):
        return self._post({"model": self.model, "prompt": prompt, "stream": False,
                           "keep_alive": KEEP_ALIVE,
                           "options": {"temperature": 0, **self.options}},
                          self.timeout)["response"]

    def _loaded(self):
        """Модель в памяти ollama (запись из /api/ps) или None."""
        try:
            with urllib.request.urlopen(self.url + "/api/ps", timeout=2) as r:
                models = json.load(r).get("models", [])
        except (OSError, ValueError):
            return None
        return next((m for m in models
                     if self.model in (m.get("name"), m.get("model"))), None)

    def _loaded_mb(self):
        """Сколько видеопамяти модель уже держит: она освободится ей же."""
        return (self._loaded() or {}).get("size_vram", 0) // 2**20

    def unload(self):
        """Выгрузить модель, если она в памяти; True — если выгрузили.

        Модель переживает перезапуск демона (keep_alive 30 минут), и тогда
        whisper грузился бы в то, что она оставила. Без проверки /api/ps
        запрос с keep_alive 0 мог бы сначала её загрузить."""
        if self._loaded() is None:
            return False
        self._post({"model": self.model, "keep_alive": 0}, self.WARM_TIMEOUT)
        return True

    def fit_gpu(self):
        """num_gpu не больше того, что влезет рядом с whisper; None — не менялось."""
        free = vram_free_mb() if self.max_gpu else None
        if free is None:
            return None
        free += self._loaded_mb()
        fit = max(0, (free - self.RESERVE_MB - self.BASE_MB) // self.LAYER_MB)
        self.options["num_gpu"] = min(self.max_gpu, fit)
        return self.options["num_gpu"]

    def warm(self):
        """Загрузить модель заранее: запрос без промпта ollama понимает так.
        Возвращает число слоёв на видеокарте, если его пришлось подобрать."""
        layers = self.fit_gpu()
        self._post({"model": self.model, "keep_alive": KEEP_ALIVE,
                    "options": self.options}, self.WARM_TIMEOUT)
        return layers


def load_config(log=print, path=CONFIG):
    """~/.config/nohands/config.toml целиком; {} — если его нет или он битый."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as ex:
        log(f"{path} не прочитан ({ex}), беру настройки по умолчанию")
        return {}


# Сказанное — в журнал только по [log] text = true. Там бывает всё, что
# диктовали, вплоть до паролей, а журнал копится месяцами.
LOG_TEXT = False


def shown(text):
    """Сказанное или вставленное — для журнала: сам текст или только длина."""
    return repr(text) if LOG_TEXT else f"<{len(text)} зн.>"


def load_llm(log=print, path=CONFIG):
    """LLM из ~/.config/nohands/config.toml; None — если выключена."""
    cfg = dict(DEFAULTS)
    cfg.update(load_config(log, path).get("shell", {}).get("llm", {}))
    if cfg["backend"] == "none":
        return None
    if cfg["backend"] != "ollama":
        log(f"консоль: неизвестный backend {cfg['backend']!r}, LLM выключена")
        return None
    options = {"num_gpu": cfg["num_gpu"]} if "num_gpu" in cfg else {}
    return Ollama(cfg["model"], cfg["url"], options=options)


def home_dir(said):
    """Домашняя папка XDG для промпта: ~/Загрузки, если она есть, иначе ~/Downloads."""
    ru = said[0].upper() + said[1:]
    return _quote("~/" + (ru if os.path.isdir(os.path.join(HOME, ru)) else SYNONYMS[said]),
                  HOME)


def prompt_for(raw, cwd):
    try:
        names = sorted(os.listdir(cwd))[:50]
    except OSError:
        names = []
    # Без объяснения про кириллицу 3B-модель не узнаёт английские слова:
    # «мау лайсенс» становилось cat README.md, «шелл точка пай» — cd.
    # С ним и тремя примерами — cat LICENSE и python shell.py.
    # «Последние пять файлов» модель понимала по алфавиту (ls | tail -n 5).
    dirs = ", ".join(f"{k} — {home_dir(k)}" for k in
                     ("загрузки", "документы", "рабочий стол", "музыка", "картинки",
                      "видео"))
    return ("Ты переводишь голосовую команду в одну строку bash.\n"
            "Команда распознана с русской речи: английские слова, имена программ и "
            "файлов часто записаны русскими буквами по звучанию (\"гит\" = git, "
            "\"лайсенс\" = LICENSE, \"пай\" = py, \"точка\" = .). Имена файлов бери из "
            "списка, если звучат похоже. Если это просьба словами — выполни её. Если это "
            "не команда и не просьба к компьютеру (разговор рядом, шум) — ответь НЕТ.\n"
            "\"Последние\", \"новые\", \"свежие\" файлы — самые новые по времени: "
            "ls -t | head, никогда не tail.\n"
            f"Домашние папки: {dirs}. Это они, а не похожие имена из списка файлов.\n"
            "Службы — systemctl; nohands — служба пользователя: systemctl --user.\n"
            "Ответь только командой, без пояснений и без markdown.\n\n"
            "Примеры:\n"
            "гит лог -> git log\n"
            "кэт ридми точка эм ди -> cat README.md\n"
            f"покажи файлы в загрузках -> ls {home_dir('загрузки')}\n"
            f"покажи последние три файла из документов -> ls -t {home_dir('документы')} "
            "| head -n 3\n"
            f"самый свежий файл в музыке -> ls -t {home_dir('музыка')} | head -n 1\n"
            "какие порты открыты -> ss -tlnp\n"
            "останови службу блютус -> sudo systemctl stop bluetooth\n"
            "ну да им нельзя плавать -> НЕТ\n\n"
            f"Каталог: {cwd}\nФайлы: {' '.join(names)}\n"
            f"Команда: {raw} ->")


def clean_reply(text):
    """Команда из ответа LLM. Модель бывает многословной («Вот команда:» и
    строкой ниже сама команда) — поэтому сначала то, что в ``` или `…`, и
    мимо строк-пояснений, оканчивающихся на двоеточие."""
    m = re.search(r"```[\w-]*\n(.*?)```", text, re.S)
    # `…` внутри готовой команды — подстановка: echo `date`
    if not m and not runnable(text.strip()):
        m = re.search(r"`([^`\n]+)`", text)
    if m:
        text = m.group(1)
    lines = [l.strip() for l in text.strip().splitlines()
             if l.strip() and not l.strip().startswith("```")
             and not l.strip().endswith(":")]
    line = lines[0] if lines else ""
    if len(line) > 1 and line[0] == line[-1] == "`":
        line = line[1:-1].strip()
    return line[2:] if line.startswith("$ ") else line


REFUSAL = "НЕТ"


def to_command(raw, cwd, llm, log=print):
    """Фраза -> команда; пустая строка — вставлять нечего.

    LLM зовём только на намерение: несколько слов, и первое — не программа.
    Одно слово («небо») или хвост с флагом («минус эл» -> -l) — продолжение
    строки или обрывок, и LLM на них только выдумывала: «море» превращала в
    cat README.md (теперь «море» — программа more, транслит совпал точно).
    """
    try:
        return _command(raw, cwd, llm, log)
    finally:
        # сбои подбора — в журнал с этой фразой, на любом выходе: runnable
        # спрашивает programs() до самого конца
        for msg in complete.drain():
            log(f"консоль: {msg}")


def _command(raw, cwd, llm, log):
    # Одна пунктуация («...», «—») — whisper на шуме, а не команда: иначе
    # она вставлялась в строку как есть.
    if not re.search(r"\w", raw):
        return ""
    cmd = rules(raw, cwd)
    toks = cmd.split()
    # whisper дописывает спереди слог: «в клауде» — это claude, а не просьба.
    # Без слога фраза разбирается заново: имя программы подбирается только
    # в начале команды.
    if len(toks) > 1 and toks[0].isalpha() and not runnable(toks[0]):
        rest = rules(" ".join(raw.split()[1:]), cwd)
        if runnable(rest):
            cmd, toks = rest, rest.split()
    if len(toks) == 1 and not runnable(cmd):
        cmd = _quote(_resolve(toks[0], cwd), cwd)        # имя файла для строки
    if (runnable(cmd) or llm is None or len(toks) < 2
            or toks[0][0] in "-~/.|<>&*$\"="):
        return cmd
    try:
        got = clean_reply(llm.complete(prompt_for(raw, cwd)))
    except Exception as ex:
        log(f"консоль: LLM не ответила ({ex}), вставляю по правилам")
        return cmd
    log(f"консоль: LLM {shown(raw)} -> {shown(got)}")
    if got.strip(".").upper() == REFUSAL:
        return ""
    return got if runnable(got) else cmd


# ──────────────────────────── cwd терминала ─────────────────────────────────
def _tty_dev(tty_nr):
    major = (tty_nr >> 8) & 0xfff
    minor = (tty_nr & 0xff) | ((tty_nr >> 12) & 0xfff00)
    return major, minor


def _foreground_shells():
    """Терминал -> каталог оболочки, которая у него на переднем плане."""
    out = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/stat") as f:
                st = f.read()
            comm = st[st.index("(") + 1:st.rindex(")")]
            fields = st[st.rindex(")") + 2:].split()
            # Лидер группы переднего плана, а не просто её член: у claude в
            # группе бывает sh от npm, и терминал с ним сходил за оболочку.
            if comm in SHELLS and pid == fields[5]:
                out[_tty_dev(int(fields[4]))] = os.readlink(f"/proc/{pid}/cwd")
        except (OSError, ValueError, IndexError):
            continue
    return out


def terminal_cwd():
    """Каталог оболочки в терминале, куда писали последним.

    Wayland не говорит, какое окно в фокусе, но вставка в терминал меняет
    mtime его /dev/pts/N — самый свежий pts и есть активный терминал. Только
    среди тех, где на переднем плане оболочка: терминал с Claude Code пишет
    постоянно и перебивал свежестью тот, куда вставляли, — cd искал папку не
    в том каталоге. Команду шеллу и так некуда вставить, кроме оболочки.
    """
    try:
        shells = _foreground_shells()
        uid = os.getuid()
        pts = []
        for p in glob.glob("/dev/pts/[0-9]*"):
            st = os.stat(p)
            dev = (os.major(st.st_rdev), os.minor(st.st_rdev))
            if st.st_uid == uid and dev in shells:
                pts.append((st.st_mtime, shells[dev]))
        return max(pts)[1] if pts else HOME
    except OSError:
        return HOME
