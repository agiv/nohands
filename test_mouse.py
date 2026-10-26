#!/usr/bin/env python3
"""Мышь командами: откат пути после «стоп», скорость, задержка по синтезу.

Запуск: ~/.local/share/nohands/venv/bin/python test_mouse.py

Устройство ввода подменено, звук синтезируется в память (см. test_commands).
"""
import json, os, sys, time, numpy as np, vosk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voiced as V

fails = []


def check(name, ok, info=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {info}")
    if not ok:
        fails.append(name)


class FakeInput:
    def __init__(self):
        self.moves = []
    def move(self, dx, dy):
        self.moves.append((dx, dy))
    def total(self):
        return sum(m[0] for m in self.moves), sum(m[1] for m in self.moves)


class Clock:
    """Часы мыши: идут только по кадрам теста. По настенным на занятой машине
    кадры терялись, и «обычная за 2 с» выходила за пределы."""
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t


def mouse(span=None, read_span=None):
    m = V.Mouse(inp := FakeInput(), span=span, read_span=read_span)
    m.clock = Clock()
    m.FRAME_S = 0                            # откат и шаг — без пауз между кадрами
    return m, inp


def frames(m, s):
    for _ in range(round(s / V.Mouse.FRAME_S)):
        m.step()
        m.clock.t += V.Mouse.FRAME_S


def drive(level, go_s, dx=1, dy=0):
    m, inp = mouse()
    m.level = level
    m.go(dx, dy)
    frames(m, go_s)
    return m, inp


def rewind(m, back_s):
    """stop() с откатом; откат идёт своим потоком, без пауз — ждать недолго."""
    m.stop(back_s=back_s)
    time.sleep(0.2)


def main():
    print("── скорость: за 2 с на обычной — 300–1400 пикселей ──")
    m, inp = drive(V.Mouse.START_LEVEL, 2.0)
    m.stop()
    px = abs(inp.total()[0])
    check("обычная за 2 с", 300 <= px <= 1400, f"({px} пикселей; 3 пикс/кадр с разгоном до 2x)")

    print("── дробные пиксели не теряются ──")
    m, inp = mouse()
    m.BASE, m.level = {2: 1.5}, 2
    m.ACCEL_MAX = 1.0
    m.go(1, 0); frames(m, 1.0); m.stop()
    n = len(inp.moves)
    check("1,5 пикс/кадр даёт в среднем 1,5, а не 1", n > 0 and abs(inp.total()[0] / n - 1.5) < 0.1,
          f"({inp.total()[0]} за {n} кадров)")

    print("── ступени скорости: каждая медленнее предыдущей, на краю шкалы упор ──")
    m = V.Mouse(FakeInput())
    seen = [m.level]
    for _ in range(8):
        m.slower(); seen.append(m.level)
    check("«медленнее» доходит до самой медленной и стоит", seen[-1] == min(m.BASE) and seen.count(min(m.BASE)) > 1, f"({seen})")
    for _ in range(8):
        m.faster()
    check("«быстрее» доходит до самой быстрой", m.level == max(m.BASE), f"({m.level})")
    speeds = [m.BASE[k] for k in sorted(m.BASE)]
    check("скорости растут", speeds == sorted(set(speeds)), f"({speeds})")
    check("обычная осталась прежней", m.BASE[V.Mouse.START_LEVEL] == 3)
    m2, inp2 = drive(1, 2.0); m2.stop()
    m3, inp3 = drive(V.Mouse.START_LEVEL, 2.0); m3.stop()
    check("самая медленная за 2 с в 3+ раза короче обычной",
          abs(inp2.total()[0]) * 3 <= abs(inp3.total()[0]), f"({inp2.total()[0]} против {inp3.total()[0]})")

    print("── откат: курсор возвращается туда, где был в начале слова ──")
    m, inp = drive(2, 1.2, dx=1, dy=1)
    at_word = inp.total()
    frames(m, 0.6)                           # слово «стоп» и хвост
    before = inp.total()
    rewind(m, 0.6)
    after = inp.total()
    dx = after[0] - at_word[0]
    check("итог близок к точке начала слова", abs(dx) <= 0.15 * abs(before[0] - at_word[0]) + 6,
          f"(было {at_word[0]}, уехало до {before[0]}, после отката {after[0]})")
    check("по осям одинаково", abs(after[0] - at_word[0]) - abs(after[1] - at_word[1]) <= 2)

    print("── откат не идёт дальше начала движения и без back_s ничего не делает ──")
    m, inp = drive(2, 0.5)
    rewind(m, 0.0)
    check("stop() без back_s не возвращает", inp.total()[0] > 0, f"({inp.total()})")
    m, inp = drive(2, 0.5)
    rewind(m, 10.0)
    check("возврат не дальше старта", abs(inp.total()[0]) <= 3, f"({inp.total()})")
    m, inp = drive(2, 0.3)
    m.stop()
    p = inp.total()[0]
    rewind(m, 1.0)
    q = inp.total()[0]
    rewind(m, 1.0)
    check("возвращённые кадры снимаются: второй откат ничего не добавляет",
          q < p and inp.total()[0] == q, f"({p} -> {q} -> {inp.total()[0]})")

    print("── «мышь выше» и соседи: шаг на символ ──")
    m, inp = mouse()
    m.nudge(-1, 0); time.sleep(0.2)
    check("левее — ширина символа", inp.total() == (-V.Mouse.CHAR_PX[0], 0), f"({inp.total()})")
    m.nudge(0, 1); time.sleep(0.2)
    check("ниже — высота строки", inp.total() == (-V.Mouse.CHAR_PX[0], V.Mouse.CHAR_PX[1]), f"({inp.total()})")
    check("по пикселю за кадр", all(abs(dx) + abs(dy) == 1 for dx, dy in inp.moves), f"({set(inp.moves)})")
    m, inp = drive(2, 0.3)
    m.nudge(0, -1); time.sleep(0.2)
    before = inp.total()[0]; frames(m, 0.2)
    check("шаг останавливает езду без отката", inp.total()[0] == before > 0
          and inp.total()[1] == -V.Mouse.CHAR_PX[1], f"({inp.total()})")

    print("── край экрана: кадры, упёршиеся в него, не откатываются ──")
    def with_trail(span, moves):
        m, inp = mouse(span)
        for i, (dx, dy) in enumerate(moves):
            m.trail.append((m.clock.t - 0.5 + i * 0.01, dx, dy))
        return m, inp
    # экран 100 пикс: после четырёх кадров по 30 курсор заведомо у края
    # (даже без ускорения он прошёл 120 из 100), пятый и шестой кадры пустые
    m, inp = with_trail((100, 100), [(30, 0)] * 6)
    rewind(m, 1.0)
    check("откат только пройденного до края", inp.total()[0] == -120,
          f"({inp.total()})")
    m, inp = with_trail((1920, 1080), [(30, 0)] * 6)
    rewind(m, 1.0)
    check("далеко от края — откат целиком", inp.total()[0] == -180, f"({inp.total()})")
    m, inp = with_trail(None, [(300, 0)] * 6)
    rewind(m, 1.0)
    check("размер экрана неизвестен — откат целиком, как раньше", inp.total()[0] == -1800, f"({inp.total()})")
    m, inp = with_trail((100, 100), [(30, 0)] * 4 + [(0, 30)] * 2)
    rewind(m, 1.0)
    check("края считаются по осям отдельно", inp.total() == (-120, -60), f"({inp.total()})")

    print("── размер экрана перечитывается в начале езды, а не на ходу ──")
    sizes = [(1920, 1080), (3840, 1080), None]
    reads = []
    def read_span():
        reads.append(1)
        return sizes[min(len(reads), len(sizes)) - 1]
    m, inp = mouse(read_span=read_span)
    m.go(1, 0)
    check("первая езда читает экран", m.span == (1920, 1080), f"({m.span})")
    m.go(0, 1)
    check("смена направления на ходу экран не читает", len(reads) == 1, f"({len(reads)})")
    m.stop(); m.go(1, 0)
    check("подключили монитор — новая рамка", m.span == (3840, 1080), f"({m.span})")
    m.stop(); m.go(1, 0)
    check("не вышло спросить — остаётся прежняя", m.span == (3840, 1080), f"({m.span})")

    print("── точка остановки — доля слова, а не его начало ──")
    e = V.Ears.__new__(V.Ears)               # без модели: нужны только поля
    e._last_run_s, e._silence = 0.6, 1.6
    check("доля 0 — начало слова", abs(e.since_word_s() - 2.2) < 1e-9, f"({e.since_word_s()})")
    check("доля 0,5 — середина слова", abs(e.since_word_s(0.5) - 1.9) < 1e-9, f"({e.since_word_s(0.5)})")
    check("доля 1 — конец слова", abs(e.since_word_s(1.0) - 1.6) < 1e-9, f"({e.since_word_s(1.0)})")

    print("── vosk: частичный результат раньше финального, начало слова верное ──")
    vosk.SetLogLevel(-1)
    import test_commands as T
    path = next(os.path.join(V.MODELS, d) for d in sorted(os.listdir(V.MODELS))
                if d.startswith("vosk-model"))
    model = vosk.Model(path)
    ears = V.Ears(model, V.COMMAND)
    lead = 0.6
    pcm = T.say("мышь влево", lead_s=lead, tail_s=2.5)
    part_t = fin_t = since = None
    for i in range(0, len(pcm) - V.BLOCK, V.BLOCK):
        t = (i + V.BLOCK) / V.RATE
        got = ears.feed(pcm[i:i + V.BLOCK].tobytes())
        if part_t is None and ears.partial() == V.norm("мышь влево"):
            part_t = t
        if got and fin_t is None:
            fin_t, since = t, ears.since_word_s()
    check("частичный результат есть", part_t is not None)
    check("частичный раньше финального минимум на секунду",
          part_t is not None and fin_t is not None and fin_t - part_t >= 1.0,
          f"(частичный {part_t}, финальный {fin_t})")
    start = fin_t - since if since is not None else None
    check("начало слова определено с точностью 0,3 с",
          start is not None and abs(start - lead) <= 0.3,
          f"(вычислено {start}, на деле {lead})")

    print("\nИТОГ:", "ПРОЙДЕН" if not fails else f"НЕ ПРОЙДЕН ({', '.join(fails)})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
