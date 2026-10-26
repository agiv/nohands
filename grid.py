#!/usr/bin/env python3
"""Сетка: курсор в точку по номеру клетки 00–99.

Экран делится на 10×10 клеток, названная клетка — ещё на 10×10 мелких;
номер второй клетки ставит курсор в её центр. Здесь — номера, геометрия и
окно сетки. Модуль не знает о демоне, микрофоне и vosk.
"""

import ctypes, os, subprocess, sys, threading

DIGITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
         "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = {2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят",
        6: "шестьдесят", 7: "семьдесят", 8: "восемьдесят", 9: "девяносто"}

# Номер говорят и числом («сорок семь», «семь»), и по цифрам («четыре семь»,
# «ноль семь») — оба вида значат одно и то же.
NUMBERS = {w: n for n, w in enumerate(DIGITS)}
NUMBERS.update({f"{a} {b}": 10 * i + j for i, a in enumerate(DIGITS)
                for j, b in enumerate(DIGITS)})
NUMBERS.update({w: 10 + n for n, w in enumerate(TEENS)})
NUMBERS.update({w: 10 * t for t, w in TENS.items()})
NUMBERS.update({f"{w} {DIGITS[j]}": 10 * t + j for t, w in TENS.items() for j in range(1, 10)})

ABS_MAX = 32767


def cell_point(rect, first, second):
    """Центр мелкой клетки second в крупной first; rect — (x, y, ширина,
    высота) монитора. Без округления до пикселя: на 1920×1080 мелкая клетка
    19,2×10,8, и округление копило бы сдвиг к правому краю."""
    x, y, w, h = rect
    cw, ch = w / 10, h / 10
    return (x + (first % 10) * cw + (second % 10 + 0.5) * cw / 10,
            y + (first // 10) * ch + (second // 10 + 0.5) * ch / 10)


def cell_center(rect, n):
    """Центр крупной клетки n — для «клик» после первого номера."""
    x, y, w, h = rect
    return x + (n % 10 + 0.5) * w / 10, y + (n // 10 + 0.5) * h / 10


def abs_value(v, extent):
    """Координата рамки -> значение ABS_X/ABS_Y устройства.

    Делитель — extent, а не extent - 1: GNOME растягивает 0..ABS_MAX на
    [0, extent), и с extent - 1 курсор вставал на 0,7 пикс правее (опыт 25.10:
    просили 1440.0, окно увидело 1440.7)."""
    return min(ABS_MAX, max(0, round(v * ABS_MAX / extent)))


# ───────────────────── окно сетки: отдельный процесс на GTK4 ──────────────────
# В процессе демона уже GTK3 трея, а GTK3 и GTK4 в одном процессе не уживаются.
# Процесс постоянный: запуск GTK4 занимает дольше, чем сетке позволено (~0,3 с
# от фразы до сетки). С демоном — строками: демон шлёт show, zoom NN, hide;
# окно отвечает ready, shown X Y W H, enter X Y, pointer X Y, hidden.

LINE = (0.9, 0.1, 0.1, 0.45)
TEXT = (0.9, 0.1, 0.1, 1.0)


def draw(cr, w, h, zoom):
    """Крупная сетка с номерами; при zoom — эта клетка поделена на мелкие."""
    import cairo
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cw, ch = w / 10, h / 10
    cr.set_source_rgba(*LINE)
    cr.set_line_width(1)
    for i in range(1, 10):
        cr.move_to(i * cw, 0); cr.line_to(i * cw, h)
        cr.move_to(0, i * ch); cr.line_to(w, i * ch)
    cr.stroke()
    # шрифт — от высоты монитора: 22 и 7 пикс на 1080 одобрены на наброске
    for n in range(100):
        if n != zoom:
            label(cr, f"{n:02d}", (n % 10 + 0.5) * cw, (n // 10 + 0.5) * ch, 22 * h / 1080)
    if zoom is None:
        return
    x0, y0 = (zoom % 10) * cw, (zoom // 10) * ch
    cr.set_source_rgba(*LINE)
    for i in range(1, 10):
        cr.move_to(x0 + i * cw / 10, y0); cr.line_to(x0 + i * cw / 10, y0 + ch)
        cr.move_to(x0, y0 + i * ch / 10); cr.line_to(x0 + cw, y0 + i * ch / 10)
    cr.stroke()
    cr.set_source_rgba(*TEXT)
    cr.set_line_width(2)
    cr.rectangle(x0, y0, cw, ch)
    cr.stroke()
    for n in range(100):
        label(cr, f"{n:02d}", x0 + (n % 10 + 0.5) * cw / 10,
              y0 + (n // 10 + 0.5) * ch / 10, 7 * h / 1080)


def label(cr, text, x, y, size):
    """Номер красным с белой обводкой: читается и на тёмном, и на светлом."""
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.move_to(x - ext.width / 2 - ext.x_bearing, y - ext.height / 2 - ext.y_bearing)
    cr.text_path(text)
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.set_line_width(max(1.0, size / 8))
    cr.stroke_preserve()
    cr.set_source_rgba(*TEXT)
    cr.fill()


def say(*words):
    print(*words, flush=True)


class XAttrs(ctypes.Structure):
    """XSetWindowAttributes из Xlib.h — нужен только override_redirect."""
    _fields_ = [(n, t) for n, t in (
        ("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong))]


CW_OVERRIDE_REDIRECT = 1 << 9


def main():
    # Окно сетки — X11 через XWayland с override-redirect, а не окно Wayland:
    # - Wayland-окно берёт фокус, и полноэкранное приложение под ним
    #   переставало быть активным (браузер выходит из полноэкранного видео,
    #   игры сворачиваются). Окно override-redirect оконный менеджер не
    #   трогает: фокуса не получает, лежит поверх всех окон, и полноэкранных
    #   тоже, и поверх панели и дока (проверено 26.10: приложение осталось
    #   активным, сетка закрыла весь экран).
    # - Полноэкранное Wayland-окно Mutter подкладывает чёрным фоном
    #   (maybe_configure_black_background в meta-window-actor-wayland.c), и
    #   сетка закрывала экран при любой отрисовке — Vulkan, GL, cairo.
    # Сетка — на весь монитор: так она достаёт и значки панели и дока.
    os.environ["LC_NUMERIC"] = "C"
    os.environ["GDK_BACKEND"] = "x11"
    import warnings
    # get_xid в GTK 4.18+ помечен устаревшим вместе со всем API X11; другого
    # способа достать окно X11 нет, а предупреждение шло бы в журнал на
    # каждую сетку
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkX11", "4.0")
    from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: F401 — GdkX11 даёт get_xid

    # Без дисплея Gtk.Window падает уже после app.hold(): процесс жил бы
    # молча, и демон ждал бы ready все START_S — 5 с глухоты на каждую
    # «мышь сетка» (служба поднялась раньше сеанса).
    if Gdk.Display.get_default() is None:
        say("ошибка: нет дисплея")
        sys.exit(1)
    xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xdpy = ctypes.c_void_p(xlib.XOpenDisplay(None))
    if not xdpy:
        say("ошибка: XWayland не открылся")
        sys.exit(1)

    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    css = Gtk.CssProvider()
    css.load_from_string("window, window.background { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    state = {"win": None, "zoom": None, "at": (0, 0)}

    def monitor_rect():
        # Монитор у пользователя один; при нескольких — первый, лишь бы не
        # упасть (спецификация большего не требует)
        g = Gdk.Display.get_default().get_monitors().get_item(0).get_geometry()
        return g.x, g.y, g.width, g.height

    def show():
        hide(quiet=True)
        state["zoom"] = None
        x, y, w, h = monitor_rect()
        state["at"] = (x, y)
        win = Gtk.Window(application=app, decorated=False, title="nohands: сетка")
        win.set_default_size(w, h)
        area = Gtk.DrawingArea()
        area.set_draw_func(lambda a, cr, w, h: draw(cr, w, h, state["zoom"]))
        win.set_child(area)
        motion = Gtk.EventControllerMotion()
        frame = lambda px, py: (px + x, py + y)
        motion.connect("enter", lambda c, px, py: say("enter", *frame(px, py)))
        motion.connect("motion", lambda c, px, py: say("pointer", *frame(px, py)))
        win.add_controller(motion)
        win.realize()
        # GTK под X11 ужимает окно до рабочей области (1853×1048 вместо
        # 1920×1080), и клетки рисовались бы не на весь монитор
        win.get_surface().connect("compute-size", lambda _, size: size.set_size(w, h))
        xid = ctypes.c_ulong(win.get_surface().get_xid())
        attrs = XAttrs(override_redirect=1)
        xlib.XChangeWindowAttributes(xdpy, xid, ctypes.c_ulong(CW_OVERRIDE_REDIRECT),
                                     ctypes.byref(attrs))
        xlib.XMoveResizeWindow(xdpy, xid, x, y, w, h)
        xlib.XFlush(xdpy)
        win.present()
        state["win"], state["area"] = win, area
        say("shown", x, y, w, h)

    def hide(quiet=False):
        if state["win"] is not None:
            state["win"].destroy()
            state["win"] = None
            if not quiet:
                # «hidden» ждёт «клик»: окно ловит щелчки, и щелчок, ушедший
                # раньше, чем XWayland снял окно, достался бы сетке
                Gdk.Display.get_default().sync()
                say("hidden")

    def command(line):
        try:
            cmd, *args = line.split()
            if cmd == "show":
                show()
            elif cmd == "zoom" and state["win"] is not None:
                state["zoom"] = int(args[0])
                state["area"].queue_draw()
            elif cmd == "hide":
                hide()
        except Exception as ex:
            say(f"ошибка: {line!r}: {type(ex).__name__}: {ex}")
        return False

    def read():
        for line in sys.stdin:
            if line.strip():
                GLib.idle_add(command, line)
        GLib.idle_add(app.quit)     # демон закрыл канал — он завершился

    def activate(_):
        app.hold()                  # без окон приложение иначе сразу выходит
        # Первое окно процесса поднимает отрисовку: без прогрева первая сетка
        # показывалась за 0,20–0,32 с, а последующие — за 0,04. С ним первая
        # тоже 0,04–0,05 (замер 26.10).
        warm = Gtk.Window(application=app)
        warm.realize()
        warm.destroy()
        threading.Thread(target=read, daemon=True).start()
        say("ready")

    app.connect("activate", activate)
    app.run([sys.argv[0]])


class Overlay:
    """Окно сетки глазами демона: запуск процесса, команды, его ответы."""

    START_S = 5.0       # столько ждём ready; GTK4 поднимается ~0,3 с

    def __init__(self, log):
        self.log = log
        self.proc = None
        self.rect = None
        self.entered = threading.Event()
        self.hidden = threading.Event()
        self.last_pointer = None
        self.trail = None           # список: сюда копятся все pointer (для проверки)
        self.ok = self._start()

    def _start(self):
        ready = threading.Event()
        self._ready = False
        try:
            # stderr — в журнал ошибок демона: туда же идут предупреждения GTK
            proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True, bufsize=1)
        except OSError as ex:
            self.log("сетка: окно не запустилось:", ex)
            return False
        self.proc = proc
        threading.Thread(target=self._read, args=(proc, ready), daemon=True,
                         name="grid").start()
        ready.wait(self.START_S)
        if not self._ready:
            self.log(f"сетка: окно не ответило за {self.START_S:.0f} с "
                     f"(код {proc.poll()}) — нет дисплея?")
            proc.kill()
            return False
        return True

    def _read(self, proc, ready):
        for line in proc.stdout:
            try:
                self._line(line, ready)
            except Exception as ex:
                # Умри читатель — stdout окна никто не читает, окно встаёт
                # на print, и «hide» до него не доходит: сетка висит над
                # экраном и ловит клики.
                self.log(f"сетка: строка окна не разобрана ({type(ex).__name__}: {ex}):",
                         line.rstrip()[:80])
        ready.set()                 # умер, не успев ответить, — не ждать зря
        if proc is self.proc:
            self.log(f"сетка: окно закрылось (код {proc.wait()})")

    def _line(self, line, ready):
        cmd, *args = line.split() or [""]
        if cmd == "ready":
            self._ready = True
            ready.set()
        elif cmd == "pointer":
            self.last_pointer = tuple(map(float, args))
            if self.trail is not None:
                self.trail.append(self.last_pointer)
        elif cmd == "enter":
            self.last_pointer = tuple(map(float, args))
            self.entered.set()
        elif cmd == "shown":
            self.rect = tuple(map(float, args))
            self.log("сетка: область", *args)
        elif cmd == "hidden":
            self.hidden.set()
        else:
            self.log("сетка:", line.rstrip())

    def _send(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def show(self):
        """Показать сетку; False — окна нет и поднять его не вышло."""
        self.rect, self.last_pointer = None, None
        self.entered.clear()
        if self.proc is None or self.proc.poll() is not None or not self._send("show"):
            self.log("сетка: окна нет, запускаю заново")
            self.ok = self._start() and self._send("show")
        return self.ok

    def zoom(self, n):
        self._send(f"zoom {n:02d}")

    def hide(self):
        self.hidden.clear()
        self._send("hide")


if __name__ == "__main__":
    main()
