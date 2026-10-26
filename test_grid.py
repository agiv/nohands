#!/usr/bin/env python3
"""Сетка: номера клеток, центр клетки, абсолютные координаты курсора.

Запуск: ~/.local/share/nohands/venv/bin/python test_grid.py [--live]

--live — настоящее окно сетки и настоящий курсор: сетка встаёт поверх
окна в фокусе, курсор ставится в клетку 47/23. Какое окно открыть, говорит
голос Piper; пользователь сидит за компьютером.
"""
import math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def check(name, ok, info=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {info}")
    if not ok:
        fails.append(name)


def near(a, b):
    return all(math.isclose(x, y, abs_tol=1e-6) for x, y in zip(a, b))


class FakeDevice:
    """UInput, который только копит события."""
    def __init__(self):
        self.events, self.syns = [], 0
    def write(self, *ev):
        self.events.append(ev)
    def syn(self):
        self.syns += 1


class FakeOverlay:
    def __init__(self):
        import threading
        self.ok, self.rect, self.calls = True, None, []
        self.hidden = threading.Event()
        self.answers = True         # окно отвечает «hidden» на hide
    def show(self):
        self.calls.append("show")
        return self.ok
    def zoom(self, n):
        self.calls.append(("zoom", n))
    def hide(self):
        self.calls.append("hide")
        if self.answers:
            self.hidden.set()


class FakeInput:
    def __init__(self):
        self.puts, self.clicks = [], []
    def put(self, x, y, span):
        self.puts.append((x, y, span))
    def click(self, btn, double=False):
        self.clicks.append((btn, double, len(self.puts)))
    def release_all(self):
        pass


class Fake:
    """Голова, мышь, уши: всё, что демон дёргает при смене режима."""
    def __init__(self):
        self.states = []
    def listen(self, state):
        self.states.append(state)
    def __getattr__(self, name):
        return lambda *a, **k: 0.0


def daemon(V, state):
    import collections
    d = V.Daemon.__new__(V.Daemon)
    d.state, d.lang, d.llm, d._llm_timer = state, "ru", None, None
    d._shell_pending = collections.deque()
    d._last_cmd, d._last_t, d._shell_used = None, 0.0, 0.0
    d.overlay, d.inp = FakeOverlay(), FakeInput()
    d.mouse, d.head, d.ears = Fake(), Fake(), Fake()
    d.grid_first, d._grid_used = None, 0.0
    return d


def modes():
    import grid as G, voiced as V
    logged = []
    V.log = lambda *a: logged.append(" ".join(map(str, a)))
    V.feedback = lambda *a, **k: None
    V.screen_size = lambda: (1920, 1080)

    print("── режим СЕТКА ──")
    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    check("«мышь сетка» — режим СЕТКА", d.state == V.GRID, f"({d.state})")
    check("сетка показана", d.overlay.calls == ["show"], f"({d.overlay.calls})")
    check("грамматика СЕТКИ", d.ears.states == [V.GRID], f"({d.ears.states})")

    d = daemon(V, V.COMMAND)
    d.overlay.ok = False
    d.run_command(("grid", None))
    check("окна нет — остаёмся в КОМАНДАХ", d.state == V.COMMAND, f"({d.state})")
    check("в журнале «сетка недоступна»", any("сетка недоступна" in l for l in logged))

    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.overlay.rect = (0, 0, 1920, 1080)
    d.handle_text("сорок семь", 1.0)
    check("первый номер — клетка крупно", d.overlay.calls[-1] == ("zoom", 47), f"({d.overlay.calls})")
    check("после первого номера — СЕТКА", d.state == V.GRID)
    check("курсор на первом шаге не двигается", not d.inp.puts)
    d.handle_text("два три", 1.0)
    check("второй номер — курсор в точку", len(d.inp.puts) == 1
          and near(d.inp.puts[0][:2], (1411.2, 459.0)) and d.inp.puts[0][2] == (1920, 1080),
          f"({d.inp.puts})")
    check("после второго номера — КОМАНДЫ", d.state == V.COMMAND, f"({d.state})")
    check("сетка закрыта", d.overlay.calls[-1] == "hide", f"({d.overlay.calls})")

    # окно ещё не сообщило монитор — по рамке screen_size()
    V.screen_size = lambda: (2560, 1440)
    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.handle_text("сорок семь", 1.0)
    d.handle_text("два три", 1.0)
    want = G.cell_point((0, 0, 2560, 1440), 47, 23)
    check("без монитора окна — по рамке", len(d.inp.puts) == 1 and near(d.inp.puts[0][:2], want)
          and d.inp.puts[0][2] == (2560, 1440), f"({d.inp.puts})")

    # Mutter не ответил — размер рамки от монитора окна
    V.screen_size = lambda: None
    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.overlay.rect = (0, 0, 1920, 1080)
    d.handle_text("сорок семь", 1.0)
    d.handle_text("два три", 1.0)
    check("без рамки — размер от монитора", len(d.inp.puts) == 1
          and d.inp.puts[0][2] == (1920, 1080), f"({d.inp.puts})")
    V.screen_size = lambda: (1920, 1080)

    for first, close in ((False, "нет"), (False, "стоп"), (True, "нет"), (True, "стоп")):
        d = daemon(V, V.COMMAND)
        d.run_command(("grid", None))
        if first:
            d.handle_text("сорок семь", 1.0)
        d.handle_text(close, 1.0)
        step = "после номера" if first else "сразу"
        check(f"«{close}» {step} — КОМАНДЫ без курсора",
              d.state == V.COMMAND and not d.inp.puts and d.overlay.calls[-1] == "hide",
              f"({d.state}, {d.inp.puts}, {d.overlay.calls})")

    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.overlay.rect = (0, 0, 1920, 1080)
    d.handle_text("сорок семь", 1.0)
    d.handle_text("клик", 1.0)
    check("«клик» после номера — курсор в центр клетки", len(d.inp.puts) == 1
          and near(d.inp.puts[0][:2], (1440.0, 486.0)), f"({d.inp.puts})")
    check("«клик» после номера — щелчок после курсора и закрытия сетки",
          d.inp.clicks == [(V.E.BTN_LEFT, False, 1)] and d.overlay.calls[-1] == "hide",
          f"({d.inp.clicks}, {d.overlay.calls})")
    check("после «клик» — КОМАНДЫ", d.state == V.COMMAND, f"({d.state})")

    for phrase, want in (("правый клик", (V.E.BTN_RIGHT, False)), ("двойной клик", (V.E.BTN_LEFT, True))):
        d = daemon(V, V.COMMAND)
        d.run_command(("grid", None))
        d.overlay.rect = (0, 0, 1920, 1080)
        d.handle_text("сорок семь", 1.0)
        d.handle_text(phrase, 1.0)
        check(f"«{phrase}» после номера — в центр клетки и щелчок",
              len(d.inp.puts) == 1 and near(d.inp.puts[0][:2], (1440.0, 486.0))
              and d.inp.clicks == [(*want, 1)] and d.state == V.COMMAND,
              f"({d.inp.puts}, {d.inp.clicks}, {d.state})")

    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.handle_text("клик", 1.0)
    check("«клик» до номера — ничего, сетка открыта",
          d.state == V.GRID and not d.inp.puts and not d.inp.clicks,
          f"({d.state}, {d.inp.puts}, {d.inp.clicks})")
    check("в журнале «сперва номер клетки»", any("сперва номер" in l for l in logged))

    d = daemon(V, V.COMMAND)
    d.GRID_HIDE_S = 0.05
    d.run_command(("grid", None))
    d.overlay.answers = False
    d.handle_text("сорок семь", 1.0)
    d.handle_text("клик", 1.0)
    check("окно не закрылось — курсор в клетке, щелчка нет",
          len(d.inp.puts) == 1 and not d.inp.clicks and d.state == V.COMMAND,
          f"({d.inp.puts}, {d.inp.clicks}, {d.state})")

    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d.set_state(V.SLEEP)
    check("смена режима из трея закрывает сетку", d.overlay.calls[-1] == "hide", f"({d.overlay.calls})")

    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d._grid_used = time.time() - V.Daemon.GRID_IDLE_S - 1
    d._grid_idle()
    check("30 с без дела — КОМАНДЫ", d.state == V.COMMAND and d.overlay.calls[-1] == "hide",
          f"({d.state}, {d.overlay.calls})")
    d = daemon(V, V.COMMAND)
    d.run_command(("grid", None))
    d._grid_idle()
    check("только что открытая сетка не закрывается", d.state == V.GRID)

    print("── грамматики ──")
    import json
    grid_g = json.loads(V.grammar_for(V.GRID))
    check("СЕТКА: номера, «нет», «стоп», клики",
          sorted(grid_g) == sorted(list(V.NORM_GRID) + ["нет", "стоп", "клик", "правый клик", "двойной клик",
                                                 "[unk]"]),
          f"({len(grid_g)})")
    cmd_g = json.loads(V.grammar_for(V.COMMAND))
    check("КОМАНДЫ: без номеров", "сорок семь" not in cmd_g and "четыре семь" not in cmd_g)
    check("КОМАНДЫ: «мышь сетка», «мышь координаты»",
          "мышь сетка" in cmd_g and "мышь координаты" in cmd_g)


def overlay_failures():
    import grid as G, tempfile, threading
    print("── окно сетки: сбои ──")
    # Без дисплея: GTK без WAYLAND_DISPLAY сам находит wayland-0 в
    # XDG_RUNTIME_DIR, поэтому каталог — пустой, иначе окно откроется.
    keep = {k: os.environ.get(k) for k in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR")}
    with tempfile.TemporaryDirectory() as empty:
        os.environ.pop("WAYLAND_DISPLAY", None); os.environ.pop("DISPLAY", None)
        os.environ["XDG_RUNTIME_DIR"] = empty
        try:
            logged = []
            t0 = time.time()
            ov = G.Overlay(lambda *a: logged.append(" ".join(map(str, a))))
            spent = time.time() - t0
        finally:
            for k, v in keep.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
    check("без дисплея — окна нет", not ov.ok)
    check("без дисплея — отказ быстро", spent < 2.0, f"({spent:.2f} с; START_S {G.Overlay.START_S})")

    # Строка, которую читатель не разобрал, не должна его убить: иначе stdout
    # окна никто не читает, окно встаёт на print, и «hide» не доходит —
    # сетка висит над экраном и ловит клики.
    ov = G.Overlay.__new__(G.Overlay)
    logged = []
    ov.log = lambda *a: logged.append(" ".join(map(str, a)))
    ov.rect = ov.last_pointer = ov.trail = None
    ov.entered = threading.Event()
    ov._ready = False
    proc = type("P", (), {"stdout": ["pointer x y\n", "pointer 1 2\n"], "wait": lambda self: 0})()
    ov.proc = proc
    try:
        ov._read(proc, threading.Event())
        survived = True
    except Exception as ex:
        survived = False
        logged.append(repr(ex))
    check("кривая строка окна — читатель жив", survived and ov.last_pointer == (1.0, 2.0),
          f"({ov.last_pointer}, {logged})")


# (что сказать перед серией, сколько раз открыть сетку): всего десять
LIVE = [("Проверка сетки. Нужен терминал в фокусе.", 4),
        ("Теперь нужен браузер в фокусе.", 3),
        ("Теперь нужно приложение на весь экран, например видео.", 3)]


def overview():
    import subprocess
    r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                        "--object-path", "/org/gnome/Shell", "--method",
                        "org.freedesktop.DBus.Properties.Get", "org.gnome.Shell",
                        "OverviewActive"], capture_output=True, text=True)
    return "true" in r.stdout


def live():
    import grid as G, speech, voiced as V
    inp = V.Input()
    ov = G.Overlay(V.log)
    check("окно запустилось", ov.ok)
    if not ov.ok:
        return
    span = V.screen_size()
    check("размер рамки", span is not None, f"({span})")
    n = 0
    for text, times in LIVE:
        # 8 с, а не 5: в первом прогоне 26.10 первая сетка над видео поймала
        # курсор в 1036, 712 — его ещё двигали, разворачивая видео.
        # Демон слушает и подсказки: «курсор не трогайте» он услышал как
        # «обзор» (0.68) — в обзоре окно уменьшено, и курсор в нём выходил
        # смещённым в 1,25 раза. Поэтому слов, похожих на команды, тут нет,
        # а обзор проверяется перед каждой сеткой.
        speech.say(text)
        time.sleep(8)
        for _ in range(times):
            # Клетки чередуются сквозь серии: курсору, уже стоящему в цели,
            # put даёт лишь шаг в 0,06 пикс и обратно — проверка была бы не
            # о том, встаёт ли он в точку.
            second = (23, 76)[n % 2]
            n += 1
            check("обзор GNOME закрыт", not overview())
            t0 = time.time()
            ov.show()
            while ov.rect is None and time.time() - t0 < 1.0:
                time.sleep(0.005)
            shown_s = time.time() - t0
            entered = ov.entered.wait(max(0.0, 0.5 - (time.time() - t0)))
            ov.zoom(47)
            if ov.rect is None:
                check("окно сообщило монитор", False)
                ov.hide()
                continue
            # Курсор — не раньше 0,6 с после показа: окно Wayland, пока GNOME
            # анимировал открытие, видело курсор сдвинутым или не видело
            # вовсе (замер 26.10: put через 0,05–0,2 с — 9 промахов из 10).
            # У окна X11 анимации нет, но живой номер и так звучит через
            # секунды — пауза ничего не прячет.
            time.sleep(max(0.0, 0.6 - (time.time() - t0)))
            x, y = G.cell_point(ov.rect, 47, second)
            # Судим по событиям сразу после put: что приходит позже — чужое
            # движение (рука на мыши). Бывает и лишнее событие перед точным
            # (0.0, 239.9 перед 1411.2, 459.0).
            trail = ov.trail = []
            inp.put(x, y, span or ov.rect[2:])
            time.sleep(0.1)
            # Окно X11 видит курсор целыми пикселями, дробь отброшена: в
            # 458,998 (замер окном Wayland) оно видит 458 — от цели 459,0
            # это 1,02. Поэтому сравниваем с целью, тоже округлённой вниз.
            hit = any(math.dist(p, (math.floor(x), math.floor(y))) <= 1.0 for p in trail)
            check("сетка за 0,3 с", shown_s <= 0.3, f"({shown_s:.3f} с)")
            check("курсор вошёл в окно", entered)
            check("курсор в точке", hit, f"(цель {x:.1f}, {y:.1f}, окно видит {trail[:6]})")
            time.sleep(0.5)
            ov.hide()
            time.sleep(0.5)
    speech.say("Проверка сетки окончена.")


def main():
    if "--live" in sys.argv:
        live()
        print("\nИТОГ:", "ПРОЙДЕН" if not fails else f"НЕ ПРОЙДЕН ({', '.join(fails)})")
        sys.exit(1 if fails else 0)
    import grid as G

    print("── номера ──")
    check("семь", G.NUMBERS["семь"] == 7)
    check("ноль семь", G.NUMBERS["ноль семь"] == 7)
    check("ноль", G.NUMBERS["ноль"] == 0)
    check("ноль ноль", G.NUMBERS["ноль ноль"] == 0)
    check("сорок семь", G.NUMBERS["сорок семь"] == 47)
    check("четыре семь", G.NUMBERS["четыре семь"] == 47)
    check("десять", G.NUMBERS["десять"] == 10)
    check("один ноль", G.NUMBERS["один ноль"] == 10)
    check("девятнадцать", G.NUMBERS["девятнадцать"] == 19)
    check("двадцать", G.NUMBERS["двадцать"] == 20)
    check("девяносто девять", G.NUMBERS["девяносто девять"] == 99)
    check("все 0..99", set(G.NUMBERS.values()) == set(range(100)))
    check("200 фраз", len(G.NUMBERS) == 200, f"({len(G.NUMBERS)})")
    check("без ё", not any("ё" in p for p in G.NUMBERS))

    print("── центр клетки ──")
    # 1920×1080: клетка 47 — 192×108, мелкая 23 — 19,2×10,8
    check("47/23", near(G.cell_point((0, 0, 1920, 1080), 47, 23), (1411.2, 459.0)))
    check("00/00", near(G.cell_point((0, 0, 1920, 1080), 0, 0), (9.6, 5.4)))
    check("99/99 у края", near(G.cell_point((0, 0, 1920, 1080), 99, 99), (1910.4, 1074.6)))
    check("смещение монитора", near(G.cell_point((1920, 0, 1920, 1080), 0, 0), (1929.6, 5.4)))

    print("── абсолютные координаты ──")
    # GNOME растягивает 0..32767 на [0, ширина): значение 24575 даёт 1439.99
    check("abs 1440", G.abs_value(1440, 1920) == 24575, f"({G.abs_value(1440, 1920)})")
    check("abs 0", G.abs_value(0, 1920) == 0)
    check("abs за краем", G.abs_value(2500, 1920) == G.ABS_MAX)
    check("abs меньше нуля", G.abs_value(-5, 1920) == 0)

    print("── Input.put ──")
    import voiced as V
    from evdev import ecodes as E
    inp = V.Input.__new__(V.Input)
    inp.abs = FakeDevice()
    inp.put(1440, 486, (1920, 1080))
    last = {c: v for _, c, v in inp.abs.events}
    check("ABS_X и ABS_Y", last == {E.ABS_X: 24575, E.ABS_Y: 14745}, f"({inp.abs.events})")
    check("syn после точки", inp.abs.syns >= 1)
    # Ядро отбрасывает EV_ABS, равное прежнему значению устройства: та же
    # клетка второй раз, после того как курсор увели головой, не сдвинула бы его.
    inp.abs = FakeDevice()
    inp.put(1440, 486, (1920, 1080))
    xs = [v for _, c, v in inp.abs.events if c == E.ABS_X]
    ys = [v for _, c, v in inp.abs.events if c == E.ABS_Y]
    check("та же точка — значения сначала другие", xs[0] != 24575 and ys[0] != 14745
          and xs[-1] == 24575 and ys[-1] == 14745, f"({inp.abs.events})")
    inp.abs = FakeDevice()
    inp.put(0, 0, (1920, 1080))
    check("в угол — шаг внутрь диапазона", all(0 <= v <= G.ABS_MAX for *_, v in inp.abs.events)
          and inp.abs.events[-1][2] == 0, f"({inp.abs.events})")
    inp.abs = FakeDevice()
    inp.put(1440, 486, None)
    check("без размера — ничего", not inp.abs.events and not inp.abs.syns)

    modes()
    overlay_failures()

    print("\nИТОГ:", "ПРОЙДЕН" if not fails else f"НЕ ПРОЙДЕН ({', '.join(fails)})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
