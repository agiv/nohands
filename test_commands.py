#!/usr/bin/env python3
"""Проверка распознавания команд на синтезированной речи.

Запуск: ~/.local/share/nohands/venv/bin/python test_commands.py

Речь синтезируется Piper (speech.synth) прямо в память — специально в обход
звуковой карты: демон слушает микрофон, и проигранная в колонки фраза была бы
им выполнена как настоящая команда.

Синтезированный голос грубее живого, поэтому несколько команд он не выговаривает
(отмечены как «не выговорил синтезатор»). Тест следит за другим: посторонняя речь
не должна порождать команды вообще.
"""
import json, os, sys, numpy as np, vosk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voiced as V
import speech

# ──────────────────────────── синтез речи в память ──────────────────────────
def say(text, lead_s=0.3, tail_s=2.0):
    """Фраза как поток PCM 16 кГц, с тишиной по краям."""
    raw, sr = speech.synth(text, exact=True)
    pcm = np.frombuffer(raw, dtype=np.int16)
    n = int(len(pcm) * V.RATE / sr)
    pcm = np.interp(np.linspace(0, len(pcm) - 1, n),
                    np.arange(len(pcm)), pcm).astype(np.int16)
    pad = lambda s: np.zeros(int(s * V.RATE), dtype=np.int16)
    return np.concatenate([pad(lead_s), pcm, pad(tail_s)])


# Без знака после слова Piper глотает конечную согласную: «таб» звучит
# 0.44 с против 0.51 у «таб.» — почти как «ка» (0.38), и vosk слышит «ка».
# В КОМАНДАХ с повторами стрелок такой «таб» грамматика и вовсе не закрывает:
# фраза приходит только по FinalResult, которого у живого потока нет.
SAID = {"таб": "таб,"}


def fires(got):
    """Выполнит ли демон то, что отдал feed: (фраза, уверенность) или None.

    Считаем только исполняемые фразы: всё прочее демон лишь пишет в журнал
    строкой «не команда». Разрушающие он ещё и отклоняет по уверенности —
    как в Daemon.handle_text."""
    if not got:
        return False
    t, conf = got
    need = V.need_conf(t, V.COMMAND)
    return (t in V.NORM_COMMANDS or t in V.NORM_EXIT or t in V.NORM_WAKE) and conf >= need


def heard_seq(model, *phrases, gap_s=0.8):
    """Несколько фраз подряд, разделённых паузой."""
    ears = V.Ears(model, V.COMMAND)
    pcm = np.concatenate([say(p, tail_s=gap_s) for p in phrases[:-1]]
                         + [say(phrases[-1])])
    out = []
    for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
        got = ears.feed(pcm[i:i + V.BLOCK].tobytes())
        if fires(got):
            out.append(got[0])
    return out


def heard(model, phrase):
    """Какие команды выдаст демон, если ему сказать эту фразу."""
    ears = V.Ears(model, V.COMMAND)
    pcm, out = say(phrase), []
    for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
        got = ears.feed(pcm[i:i + V.BLOCK].tobytes())
        if fires(got):
            out.append(got[0])
    return out


# ─────────────────────────────── что проверяем ──────────────────────────────
# Обычная речь рядом с компьютером: разговор, чтение вслух, звук из колонок.
# Ни одна из этих фраз не должна включить диктовку, слежение или нажать клавишу.
FOREIGN = [
    "сегодня хорошая погода надо сходить за хлебом",
    "я думаю что это не самая удачная идея давай обсудим завтра",
    "смотри какая голова у этой статуи в музее",
    "надо печатать документы на принтере в понедельник",
    "он сказал что будет дальше разбираться сам",
    "мы поговорим об этом позже хорошо",
    "давай я тебе покажу как это работает на практике",
    "нет это совсем не то что я имел в виду",
    "потом посмотрим что из этого получится",
    "мне кажется здесь нужно другое решение",
    "в следующий раз надо сделать по другому",
    "хорошо тогда я перезвоню тебе вечером после работы",
    "я вчера долго думал об этом вопросе и пришёл к выводу что нам стоит "
    "попробовать другой подход потому что старый способ давно не работает",
    "смотри тут написано что нужно сначала открыть настройки потом выбрать "
    "нужный раздел и только после этого менять параметры которые нас интересуют",
    # числа и «раз» — половина фраз повтора
    "я ему три раза звонил и никто не ответил",
    "пять раз проверил и всё равно ошибка",
    "стрелка на часах показывает десять",
]

# Команды, которые обязаны срабатывать. Режимные — первыми: их ложное
# срабатывание пользователь замечает сразу, а пропуск ломает работу целиком.
REAL = ["печатать", "писать", "голова", "английский", "консоль", "сон",
        "клик", "стоп", "центр", "ввод", "отмена", "обзор", "таб", "копировать",
        "правый клик", "двойной клик", "переключить окно", "конец строки",
        "вариант два", "мышь влево", "мышь сетка", "мышь координаты",
        "закрыть окно", "скриншот",
        "стрелка вниз три раза", "стрелка влево пять раз", "колесо вниз два раза",
        "стереть десять раз", "удалить четыре раза"]


def main():
    vosk.SetLogLevel(-1)
    path = next(os.path.join(V.MODELS, d) for d in sorted(os.listdir(V.MODELS))
                if d.startswith("vosk-model"))
    model = vosk.Model(path)

    print("── все слова команд есть в словаре vosk ──")
    words, missing = V.vocab_check(model)
    print(f"  {'FAIL' if missing else 'ok  '} {len(words)} слов"
          + (f", нет в словаре: {', '.join(missing)}" if missing else ""))

    print("── посторонняя речь не должна давать команд ──")
    false_fires = 0
    for p in FOREIGN:
        got = heard(model, p)
        false_fires += len(got)
        mark = "ok  " if not got else "ЛОЖНО"
        print(f"  {mark} {p[:58]:<58} {got if got else ''}")

    # Все эти фразы без приманок (WAKE_DECOYS) будили систему с уверенностью
    # 1.00. Отобраны те, что с приманками проходят: «проще» и «песня» будят
    # и с ними (0.79 и 0.70).
    print("\n── во сне посторонняя речь не будит ──")
    for p in ("он просто так сказал", "я спросил её вчера", "а ты проверил почту",
              "пожалуйста", "получилось", "произошло", "прошлый раз",
              "пусть будет так", "проснулись все", "простой вопрос", "профессия"):
        ears, pcm = V.Ears(model, V.SLEEP), say(p)
        got = [g[0] for i in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes())) and g[0] in V.NORM_WAKE]
        false_fires += len(got)
        print(f"  {'ok  ' if not got else 'ЛОЖНО'} {p:<58} {got if got else ''}")

    print("\n── настоящие команды должны срабатывать ──")
    missed, mute = [], []
    for c in REAL:
        got = heard(model, SAID.get(c, c))
        if V.norm(c) in got:
            print(f"  ok    {c}")
        else:
            # Отличаем «логика отвергла» от «синтезатор не выговорил»
            raw = V.norm(json.loads(
                vosk_plain(model, say(SAID.get(c, c)))).get("text", ""))
            (mute if raw != V.norm(c) else missed).append(c)
            why = "не выговорил синтезатор" if raw != V.norm(c) else "ОТВЕРГНУТА ЛОГИКОЙ"
            print(f"  {'--' if raw != V.norm(c) else 'FAIL'}  {c:<20} {why} (слышно: {raw!r})")

    # Защита не должна мешать работать сразу после разговора.
    print("\n── команда после посторонней речи и паузы ──")
    after = []
    for talk, cmd in [("мы поговорим об этом позже хорошо", "печатать"),
                      ("нет это совсем не то что я имел в виду", "голова"),
                      ("потом посмотрим что из этого получится", "клик")]:
        got = heard_seq(model, talk, cmd)
        ok = got == [V.norm(cmd)]
        after.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} «{talk[:34]}…» пауза «{cmd}» -> {got}")

    # Выход из диктовки и пробуждение барьерам не подчиняются: «конец текста»
    # говорят без паузы, сразу после надиктованного, а не выйти — хуже, чем
    # выйти лишний раз.
    print("\n── выход из диктовки и пробуждение ──")
    modes = []
    checks = [
        (V.DICTATION, "конец текста в конце длинной фразы", V.NORM_EXIT,
         say("я хочу записать эту мысль целиком и полностью до самого конца "
             "конец текста")),
        (V.DICTATION, "хватит печатать", V.NORM_EXIT, say("хватит печатать")),
        (V.SHELL, "хватит консоль", V.NORM_SHELL_EXIT, say("хватит консоль")),
        (V.SHELL, "хватит консоль в конце фразы", V.NORM_SHELL_EXIT,
         say("гит статус хватит консоль")),
        (V.SHELL, "ввод", [V.NORM_RUN], say("ввод")),
        (V.SHELL, "нет", [V.NORM_UNDO], say("нет")),
        # «буква це» вместо «буква цэ» — та же буква: засчитываем по результату
        *[(V.SHELL, k, [p for p, v in V.NORM_SHELL_KEYS.items() if v == V.NORM_SHELL_KEYS[k]],
           say(SAID.get(k, k))) for k in V.NORM_SHELL_KEYS],
        # «це» нет в грамматике — двойник «цэ» отбирал у него уверенность
        (V.SHELL, "контрол це", ["контрол цэ"], say("контрол це")),
        (V.SLEEP, "проснись", list(V.NORM_WAKE), say("проснись")),
        (V.SLEEP, "проснись в конце фразы", list(V.NORM_WAKE),
         say("так подожди секунду проснись")),
    ]
    for state, name, wanted, pcm in checks:
        ears = V.Ears(model, state)
        # Строгие фразы демон ещё и отклоняет по уверенности (MIN_CONF). У
        # фраз-двойников («контрол цэ» и «контрол це») vosk делит её поровну.
        got = [g for i in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes()))]
        ok = any(x in wanted and (x not in V.STRICT or c >= V.MIN_CONF) for x, c in got)
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<38} -> {[(x, round(c, 2)) for x, c in got]}")

    # Однозначный номер ждёт продолжения («четыре» → «четыре семь»), и vosk
    # закрывает его позже двузначного — по спецификации на ~0,5 с. Задержка
    # печатается как замер, проверяется только то, что номер засчитан.
    print("\n── СЕТКА: номера обоих видов, «нет», «стоп», «клик» ──")
    for phrase in ("семь", "ноль семь", "сорок семь", "четыре семь", "десять",
                   "девяносто девять", "нет", "стоп", "клик", "правый клик", "двойной клик"):
        ears = V.Ears(model, V.GRID)
        pcm = say(phrase)
        end_s = len(pcm) / V.RATE - 2.0         # конец речи: дальше tail_s тишины
        got, at = [], None
        for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
            g = ears.feed(pcm[i:i + V.BLOCK].tobytes())
            if g and (g[0] in V.NORM_GRID or g[0] in V.NORM_GRID_CLOSE
                      or g[0] in V.NORM_GRID_CLICK):
                got.append(g[0])
                at = at or (i + V.BLOCK) / V.RATE - end_s
        ok = got == [V.norm(phrase)]
        modes.append(ok)
        delay = f"через {at:.2f} с после речи" if at is not None else ""
        print(f"  {'ok  ' if ok else 'FAIL'} «{phrase}» -> {got} {delay}")

    # «ввод» жмёт Enter — внутри фразы он выполнил бы недоговорённую команду.
    # В отличие от выхода, хвостом за речью он не принимается.
    print("\n── «ввод», «нет» и клавиши внутри фразы не срабатывают ──")
    for talk in ("эхо ввод данных", "надо сделать ввод",
                 "нет это не то", "я думаю что нет",
                 "надо подняться выше вверх", "посмотри что там влево",
                 "в контрол цэ нажми", "в контрол си нажми", "потом контрол дэ нажму", "эф один это справка",
                 "надо стереть этот файл", "удалить потом не забудь",
                 "какая буква тут стоит", "буква игрек в конце слова"):
        ears = V.Ears(model, V.SHELL)
        pcm = say(talk)
        got = [g[0] for i in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes()))]
        ok = not set(got) & ({V.NORM_RUN, V.NORM_UNDO} | set(V.NORM_SHELL_KEYS))
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} «{talk}» -> {got}")

    # Нерасслышанный «ввод» повторяют, пока первый ещё не засчитан: грамматика
    # отдаёт оба после второго, и барьер судил их по одному потоку — оба
    # отклонены (журнал 28.09, 16:51 — «2 слов» дважды в одну секунду) или
    # оба нажаты. Отзвук комнаты в паузе — шум выше порога: из-за него поток
    # не закрывается. Порознь сказанные «ввод» — два Enter, как и раньше.
    print("\n── повтор «ввод» — один Enter, чужое слово рядом — ни одного ──")
    rng = np.random.default_rng(0)
    for name, parts, want in [
            ("ввод ввод, пауза 0.4", ["ввод", 0.4, 0, "ввод"], 1),
            ("ввод ввод, пауза 0.6", ["ввод", 0.6, 0, "ввод"], 1),
            ("ввод ввод, отзвук 0.6", ["ввод", 0.6, 100, "ввод"], 1),
            ("ввод ввод, отзвук 1.0", ["ввод", 1.0, 100, "ввод"], 1),
            ("ввод ввод ввод", ["ввод", 0.6, 0, "ввод", 0.6, 0, "ввод"], 1),
            ("ввод, 2 с, ввод", ["ввод", 2.0, 0, "ввод"], 2),
            ("надо ввод ввод, отзвук", ["надо ввод", 0.6, 100, "ввод"], 0)]:
        chunks, i = [say(parts[0], tail_s=0)], 1
        while i < len(parts):
            gap, lvl, word = parts[i:i + 3]
            chunks += [rng.normal(0, lvl, int(gap * V.RATE)).astype(np.int16),
                       say(word, lead_s=0, tail_s=2.0 if i + 3 >= len(parts) else 0)]
            i += 3
        pcm = np.concatenate(chunks)
        ears = V.Ears(model, V.SHELL, gate=80)
        got = [g[0] for j in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[j:j + V.BLOCK].tobytes()))]
        ok = got.count(V.NORM_RUN) == want
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<24} -> {got} (надо Enter: {want})")

    # У грамматики КОНСОЛИ мало фраз, и похожие по звучанию слова притягиваются
    # к «ввод» — Enter вместо слова. Их забирают приманки SHELL_DECOYS.
    print("\n── «claude» и «code» — не «ввод» ──")
    for word in ("клод", "клауд", "клоуд", "клауди", "код", "коуд"):
        ears = V.Ears(model, V.SHELL)
        pcm = say(word)
        got = [g[0] for i in range(0, len(pcm) - V.BLOCK, V.BLOCK)
               if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes()))]
        ok = not set(got) & ({V.NORM_RUN, V.NORM_UNDO} | set(V.NORM_SHELL_KEYS))
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} «{word}» -> {got}")

    # whisper забирает буфер после 0.9 с тишины, а vosk засчитывает фразу
    # позже — и whisper вставлял саму команду текстом («нис», «ф»). В этот
    # момент частичный результат vosk уже команда: демон придерживает буфер.
    print("\n── команда КОНСОЛИ: буфер ждёт vosk, а не уходит в whisper ──")
    for word, is_cmd in (("вниз", True), ("эф три", True), ("стереть", True),
                         ("гит статус", False), ("эм си", False)):
        ears = V.Ears(model, V.SHELL, gate=80)
        pcm = say(word, tail_s=2.0)
        daemon = type("D", (), {"ears": ears})()
        sil, seen, held, fired = 0.0, False, None, False
        for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
            b = pcm[i:i + V.BLOCK]
            g = ears.feed(b.tobytes())
            fired = fired or bool(g and (g[0] in V.NORM_SHELL_KEYS or g[0] in V.STRICT))
            loud = float(np.sqrt(np.mean(b.astype(np.float32) ** 2))) > 80
            seen, sil = (True, 0.0) if loud else (seen, sil + V.BLOCK / V.RATE)
            if seen and sil >= 0.9:          # SILENCE_S: тут whisper забрал бы буфер
                held = fired or V.Daemon._command_in_flight(daemon)
                break
        ok = held == is_cmd
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} «{word}»: {'ждём vosk' if held else 'в whisper'}")

    # Фрагмент закрывается 0.9 с тишины, а vosk отдаёт выход тем же блоком или
    # позже, и партиал в этот миг уже пуст: whisper вставлял «хуай» (25.10).
    # Такой фрагмент демон узнаёт по времени: речь в нём заходит за начало
    # фразы выхода. Сказанное перед выходом через паузу — не заходит.
    print("\n── выход из КОНСОЛИ: фрагмент с ним самим не вставляется ──")
    rng = np.random.default_rng(1)
    quiet = lambda pcm, peak: (pcm * (peak / np.abs(pcm).max())
                               + rng.normal(0, 45, len(pcm))).clip(-32768, 32767).astype(np.int16)
    for name, pcm, want in [
            ("гит статус … хватит консоль",
             np.concatenate([say("гит статус", tail_s=1.0), say("хватит консоль", lead_s=0)]),
             ["вставить"]),
            ("тихое хватит консоль", quiet(say("хватит консоль"), 1200), ["не вставлять"]),
            ("тихое конец текста", quiet(say("конец текста"), 1200), ["не вставлять"])]:
        ears = V.Ears(model, V.SHELL, gate=90)
        sil, seen, frags = 0.0, False, []
        for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
            b = pcm[i:i + V.BLOCK]
            g = ears.feed(b.tobytes())
            loud = float(np.sqrt(np.mean(b.astype(np.float32) ** 2))) > 90
            seen, sil = (True, 0.0) if loud else (seen, sil + V.BLOCK / V.RATE)
            if seen and sil >= 0.9:          # SILENCE_S: тут whisper забрал бы буфер
                frags.append(ears.now() - sil)
                seen = False
            if g and g[0] in V.NORM_SHELL_EXIT:
                break
        got = ["не вставлять" if until > ears.said_at else "вставить" for until in frags]
        ok = got == want
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<28}: речь до {[f'{u:.2f}' for u in frags]},"
              f" выход с {ears.said_at:.2f} -> {got}")

    # На «ввод» из буфера whisper срезается хвост: сама фраза и тишина после
    # неё. Срежешь мало — whisper услышит «вот» и вставит его вместо Enter;
    # срежешь много — потеряешь недопечатанную команду перед «ввод».
    print("\n── «ввод» срезается из буфера целиком, команда перед ним — нет ──")
    silence = np.zeros(V.RATE, dtype=np.int16)
    # пауза перед «ввод» во втором случае ~0.7 с: короче SILENCE_S, команда
    # ещё не распознана и лежит в буфере
    for name, before, pcm in [
            ("ввод после паузы", False, np.concatenate([silence, say("ввод")])),
            ("гит статус … ввод", True,
             np.concatenate([say("гит статус", tail_s=0.4), say("ввод")]))]:
        ears = V.Ears(model, V.SHELL, gate=80)
        for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
            # срез считаем от самого «ввода»: числа в грамматике делают из
            # «гит статус» фразу вроде «десять раз», и она приходит раньше
            if (g := ears.feed(pcm[i:i + V.BLOCK].tobytes())) and g[0].endswith(V.NORM_RUN):
                upto = pcm[:i + V.BLOCK]
                break
        left = upto[:len(upto) - int(ears.tail_s() * V.RATE)]
        blocks = [left[j:j + V.BLOCK] for j in range(0, len(left) - V.BLOCK + 1, V.BLOCK)]
        loud = [float(np.sqrt(np.mean(b.astype(np.float32) ** 2))) > 80 for b in blocks]
        tail_quiet = not any(loud[-2:])      # хвост «ввода» не остался
        kept = any(loud) == before           # команда перед ним — осталась
        ok = tail_quiet and kept
        modes.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<22}: "
              f"срез {ears.tail_s():.2f} с, осталось {len(left)/V.RATE:.2f} с, речь в остатке: {any(loud)}")

    print(f"\nложных срабатываний: {false_fires} (должно быть 0)")
    print(f"команд отвергнуто логикой: {len(missed)} (должно быть 0) {missed if missed else ''}")
    print(f"не выговорил синтезатор: {len(mute)} {mute if mute else ''}")
    ok = false_fires == 0 and not missed and all(after) and all(modes) and not missing
    print("\nИТОГ:", "ПРОЙДЕН" if ok else "ПРОВАЛЕН")
    return 0 if ok else 1


def vosk_plain(model, pcm):
    """Что слышит грамматика без всякой защиты — чтобы отличить немой синтез."""
    g = vosk.KaldiRecognizer(model, V.RATE, V.grammar_for(V.COMMAND))
    g.SetWords(True)
    for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
        g.AcceptWaveform(pcm[i:i + V.BLOCK].tobytes())
    return g.FinalResult()


if __name__ == "__main__":
    sys.exit(main())
