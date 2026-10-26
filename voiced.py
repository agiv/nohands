#!/usr/bin/env python3
"""Голосовое управление компьютером для GNOME/Wayland.

Архитектура:
  микрофон -> vosk (команды, грамматика, ~200мс)  -> uinput (скан-коды)
           -> whisper (диктовка, GPU или CPU)     -> xclip + Ctrl+V (Unicode)

Четыре состояния: СОН -> КОМАНДЫ <-> ДИКТОВКА
                                <-> КОНСОЛЬ (команды шелла, см. shell.py)
"""

import collections, concurrent.futures, glob, json, math, os, queue, re, signal, subprocess, sys, threading, time, traceback
# Gtk при импорте ставит локаль процесса из окружения, а с LC_NUMERIC=ru_RU
# vosk пишет дроби в JSON через запятую — json.loads падает на каждой фразе.
# setlocale в потоке трея успевал не всегда: конструктор ждёт его лишь 5 с,
# и при медленном старте GTK vosk загружался раньше. Окружение Gtk прочитает
# само, когда бы ни импортировался.
os.environ["LC_NUMERIC"] = "C"
import numpy as np
import sounddevice as sd
from evdev import AbsInfo, UInput, ecodes as E
from tray import Tray
from speech import WAKE_PAD_S
import shell
import grid

RATE, BLOCK = 16000, 2000          # 2000 сэмплов = 125 мс
# Код живёт в репозитории, а тяжёлое — venv, модели (1.6 ГБ) и журналы —
# в каталоге данных: в git им не место.
HOME = os.path.expanduser("~/.local/share/nohands")
CODE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HOME, "models")
SOUNDS = "/usr/share/sounds/freedesktop/stereo"

SLEEP, COMMAND, DICTATION, SHELL, GRID = "СОН", "КОМАНДЫ", "ДИКТОВКА", "КОНСОЛЬ", "СЕТКА"


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ─────────────────────────── слой ввода в систему ───────────────────────────
class Input:
    """Виртуальные клавиатура и мышь через /dev/uinput.

    Две отдельные устройства, а не одно: GNOME классифицирует устройство по
    его возможностям, и смешанное клавиатура+мышь распознаётся нестабильно.

    Третье — указатель с абсолютными координатами для сетки. Относительной
    мышью в точку не попасть: GNOME меняет скорость её движений, сдвиг на
    5 пикс дал 1,7 (замер 25.10). Без кнопок udev не счёл бы его мышью.
    """

    def __init__(self):
        # KEY_MAX и KEY_CNT — служебные границы диапазона, а не клавиши:
        # ядро отвергает попытку их включить (EINVAL)
        keys = sorted({getattr(E, k) for k in dir(E) if k.startswith("KEY_")
                       and k not in ("KEY_MAX", "KEY_CNT")
                       and 0 < getattr(E, k) < E.KEY_MAX})
        self.kb = UInput({E.EV_KEY: keys}, name="nohands-keyboard", version=1)
        self.ms = UInput(
            {E.EV_KEY: [E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE],
             E.EV_REL: [E.REL_X, E.REL_Y, E.REL_WHEEL]},
            name="nohands-mouse", version=1)
        axis = AbsInfo(value=0, min=0, max=grid.ABS_MAX, fuzz=0, flat=0, resolution=0)
        self.abs = UInput(
            {E.EV_KEY: [E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE],
             E.EV_ABS: [(E.ABS_X, axis), (E.ABS_Y, axis)]},
            name="nohands-pointer", version=1)
        time.sleep(0.3)      # устройствам нужно время «осесть» в композиторе
        log("устройства ввода созданы")

    def tap(self, *codes):
        """Нажать сочетание: все модификаторы вниз, последняя клавиша, всё вверх."""
        for c in codes:
            self.kb.write(E.EV_KEY, c, 1)
        self.kb.syn()
        time.sleep(0.02)
        for c in reversed(codes):
            self.kb.write(E.EV_KEY, c, 0)
        self.kb.syn()

    def click(self, btn=E.BTN_LEFT, double=False):
        # Сначала отпустить, и только потом нажимать. Ядро отбрасывает EV_KEY,
        # не меняющий состояние кнопки: после «зажать» без «отпустить» нажатие
        # пропадёт молча, наружу уйдёт одно отпускание — клика не будет.
        # Если кнопка свободна, это отпускание ядро отбросит само, вреда нет.
        self.ms.write(E.EV_KEY, btn, 0); self.ms.syn()
        for _ in range(2 if double else 1):
            self.ms.write(E.EV_KEY, btn, 1); self.ms.syn()
            time.sleep(0.02)
            self.ms.write(E.EV_KEY, btn, 0); self.ms.syn()
            if double:
                time.sleep(0.05)

    def hold(self, btn=E.BTN_LEFT, down=True):
        self.ms.write(E.EV_KEY, btn, 1 if down else 0); self.ms.syn()

    def release_all(self):
        """Снять все кнопки. Зажатие иначе живёт вечно и ничем себя не выдаёт."""
        for btn in (E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE):
            self.ms.write(E.EV_KEY, btn, 0)
        self.ms.syn()

    def move(self, dx, dy):
        if dx: self.ms.write(E.EV_REL, E.REL_X, int(dx))
        if dy: self.ms.write(E.EV_REL, E.REL_Y, int(dy))
        self.ms.syn()

    def wheel(self, n):
        self.ms.write(E.EV_REL, E.REL_WHEEL, int(n)); self.ms.syn()

    def put(self, x, y, span):
        """Курсор в точку рамки; span — её (ширина, высота), см. screen_size().
        Без размера точку не перевести в значения устройства — не двигаем."""
        if not span:
            return
        vx, vy = grid.abs_value(x, span[0]), grid.abs_value(y, span[1])
        # Ядро отбрасывает EV_ABS, равное прежнему значению устройства, а
        # голова и «мышь …» двигают курсор другими устройствами: та же клетка
        # второй раз оставила бы курсор там, куда его увели. Сначала шаг на
        # единицу в сторону (0,06 пикс), потом сама точка.
        step = lambda v: v - 1 if v else v + 1
        for pair in ((step(vx), step(vy)), (vx, vy)):
            self.abs.write(E.EV_ABS, E.ABS_X, pair[0])
            self.abs.write(E.EV_ABS, E.ABS_Y, pair[1])
            self.abs.syn()


# ──────────────────────────── непрерывное движение ───────────────────────────
def screen_size():
    """(ширина, высота) рамки, охватывающей все мониторы, в логических пикселях.

    Положение курсора в GNOME на Wayland прочитать нельзя (Shell.Eval закрыт),
    но размер экрана Mutter отдаёт. None — если спросить не вышло.
    """
    try:
        from gi.repository import Gio
        r = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
            Gio.DBusCallFlags.NONE, 2000, None).unpack()
        _, monitors, logical, _ = r
        modes = {m[0][0]: next(x for x in m[1] if x[6].get("is-current")) for m in monitors}
        right = bottom = 0
        for x, y, scale, transform, _, specs, _ in logical:
            w, h = modes[specs[0][0]][1:3]
            if transform % 2:                   # повёрнут на 90° или 270°
                w, h = h, w
            right, bottom = max(right, x + w / scale), max(bottom, y + h / scale)
        return (right, bottom) if right and bottom else None
    except Exception as ex:
        log("размер экрана неизвестен, край не учитывается:", ex)
        return None


class Mouse(threading.Thread):
    """Курсор едет, пока не скажут «стоп». С разгоном — далёкие цели достижимы."""

    # Пикселей за кадр (60 кадров в секунду): медленно / обычно / быстро.
    # Прежние 3 / 7 / 14 с разгоном втрое давали 420 → 1260 пикс/с на обычной
    # скорости, и «стоп» приходил, когда цель уже далеко позади (сеанс 28.09:
    # за полторы минуты восемь «стопов», от «мышь …» до «стоп» 1–2 с).
    # 2 / 4 / 9 с разгоном 2,5x всё равно оказались быстрыми: на экране
    # ~580 пикс/с через секунду езды и ~1000 при полном разгоне (замер 29.09,
    # ускорение указателя даёт ещё 1,0–2,0x к отправленному).
    # Ступеней пять: при трёх «медленнее» упиралось в дно после одного-двух
    # раз, а дно (1,5 пикс/кадр, 117–152 пикс/с на экране) оставалось быстрым
    # для точного наведения, и лишние «медленнее» молча ничего не меняли
    # (журнал 29.09: пять «медленнее» подряд, три из них на дне).
    BASE = {1: 0.6, 2: 1.0, 3: 1.5, 4: 3, 5: 6}
    START_LEVEL = 4                     # прежняя «обычная»
    ACCEL_MAX, ACCEL_S = 2.0, 2.0       # разгон в столько раз за столько секунд
    FRAME_S = 0.016
    TRAIL_S = 5.0                       # сколько пройденного пути помним
    REWIND_MAX_S = 2.5                  # дальше этого назад не возвращаем
    # Откат — не к началу слова «стоп», а к этой его доле (0 — начало, 1 — конец).
    # Замер 29.09: откат по экрану равен пройденному вперёд (342 против 347 пикс,
    # ускорение GNOME ни при чём), но слово идёт ~0,7 с, и весь этот путь —
    # 250–550 пикс на обычной — возвращался назад: «стоп» начинают говорить
    # заранее, до цели. Подбирать по ощущению.
    WORD_SHARE = 0.5
    # Во сколько раз курсор на экране едет дальше отправленных пикселей:
    # ускорение указателя GNOME. Замер 29.09: 1,03 на «медленно» и до 2,0 на
    # «быстро». Для вывода «заведомо у края» берём нижнюю границу, иначе
    # откат срезался бы там, где курсор ещё ехал.
    SCREEN_PER_SENT_MIN = 1.0

    def __init__(self, inp, span=None, read_span=None):
        super().__init__(daemon=True)
        self.inp, self.dir, self.level, self.t0 = inp, (0, 0), self.START_LEVEL, 0.0
        self.clock = time.time
        # (ширина, высота) экрана. Курсор у края стоит на месте, а кадры в
        # trail всё равно записаны как отправленные: откат вернул бы их все и
        # утащил курсор от края дальше начала слова (замер 29.09: на 178 пикс).
        # read_span перечитывает её в начале каждой езды: монитор подключают и
        # масштаб меняют, не перезапуская демон. Подписка на MonitorsChanged
        # требует главного цикла GLib, а он есть только у трея и только при
        # дисплее; вызов же стоит 2–4 мс (замер 25.10) против ~1,6 с до «стоп».
        self.span, self.read_span = span, read_span
        # Пока курсор стоит, поток спит на событии, а не просыпается 60 раз в
        # секунду: на ноутбуке это батарея.
        self.moving = threading.Event()
        self.lock = threading.Lock()
        # Что реально отправлено: (время, dx, dy). «Стоп» засчитывается уже
        # через ~1,6 с после слова, и всё это время курсор едет; смещения
        # задаём мы сами, поэтому путь с начала слова можно вернуть.
        self.trail = collections.deque()
        self._frac = [0.0, 0.0]         # дробная часть пикселя: int() её терял

    def go(self, dx, dy):
        if self.read_span and not self.moving.is_set():
            span = self.read_span()
            if span and span != self.span:
                log(f"мышь: экран {span[0]:.0f}x{span[1]:.0f}")
                self.span = span
        with self.lock:
            self.dir, self.t0 = (dx, dy), self.clock()
            self._frac = [0.0, 0.0]
            self.moving.set()

    # Шаг «мышь выше» — символ терминала: Ubuntu Sans Mono 11 по Pango — 8
    # пикс в ширину, строка 18 (замер 07.10). Едет по пикселю за кадр: на
    # таких скоростях ускорение GNOME — 1,03 (см. SCREEN_PER_SENT_MIN), а
    # одним рывком в 18 пикс оно сместило бы курсор дальше. Другой шрифт или
    # масштаб — [mouse] char_px в config.toml.
    CHAR_PX = (8, 18)

    def nudge(self, dx, dy):
        """Сдвинуть на символ. Езду останавливает без отката: шаг говорят,
        уже глядя на цель, и откат увёл бы от неё."""
        self.stop()
        def walk():
            for _ in range(max(abs(dx) * self.CHAR_PX[0], abs(dy) * self.CHAR_PX[1])):
                self.inp.move(dx, dy)
                time.sleep(self.FRAME_S)
        threading.Thread(target=walk, daemon=True).start()

    def stop(self, back_s=0.0):
        """Остановить курсор; back_s > 0 — вернуть путь за последние back_s секунд."""
        with self.lock:
            self.moving.clear()
            self.dir = (0, 0)
            back = self._take_trail(back_s)
        if back:
            log(f"мышь: откат {len(back)} кадров, {-sum(b[1] for b in back)},"
                f" {-sum(b[2] for b in back)} пикс")
            threading.Thread(target=self._rewind, args=(back,), daemon=True).start()

    def _take_trail(self, back_s):
        if back_s <= 0:
            return []
        since = self.clock() - min(back_s, self.REWIND_MAX_S)
        effective = self._effective()
        back = []
        while self.trail and self.trail[-1][0] >= since:
            self.trail.pop()
            frame = effective.pop()
            if frame[1] or frame[2]:
                back.append(frame)
        return back                     # от последнего кадра к первому

    def _effective(self):
        """Кадры trail без тех частей, что заведомо упёрлись в край экрана.

        Откуда курсор начал, неизвестно: он где-то в [0, размер]. Считаем
        интервал возможных положений по каждой оси; когда нижняя граница
        дошла до края, курсор там наверняка и следующие кадры к краю пустые.
        Вблизи края с неизвестного места это не поймать — только долгий путь.
        """
        if not self.span:
            return list(self.trail)
        lo, hi = [0.0, 0.0], list(self.span)
        r_min, r_max = self.SCREEN_PER_SENT_MIN, 3.0
        out = []
        for t, *d in self.trail:
            kept = []
            for i, v in enumerate(d):
                if v > 0:
                    if lo[i] >= self.span[i]:
                        v = 0
                    lo[i] = min(self.span[i], lo[i] + v * r_min)
                    hi[i] = min(self.span[i], hi[i] + v * r_max)
                elif v < 0:
                    if hi[i] <= 0:
                        v = 0
                    hi[i] = max(0.0, hi[i] + v * r_min)
                    lo[i] = max(0.0, lo[i] + v * r_max)
                kept.append(v)
            out.append((t, *kept))
        return out

    def _rewind(self, back):
        # Теми же кадрами, а не одним рывком: ускорение указателя в GNOME
        # зависит от скорости, и большой шаг сместил бы курсор иначе.
        for _, dx, dy in back:
            self.inp.move(-dx, -dy)
            time.sleep(self.FRAME_S)

    def faster(self):
        self._set_level(min(max(self.BASE), self.level + 1))

    def slower(self):
        self._set_level(max(min(self.BASE), self.level - 1))

    def _set_level(self, level):
        edge = " (предел)" if level == self.level else ""
        self.level = level
        log(f"мышь: скорость {level} из {max(self.BASE)}{edge}")

    def run(self):
        while True:
            self.moving.wait()
            self.step()
            time.sleep(self.FRAME_S)

    def step(self):
        """Один кадр езды. Отдельно от run(): тест гоняет кадры по своим часам
        (clock), и на занятой машине потерянные кадры не меняют итог."""
        with self.lock:
            dx, dy = self.dir
            if dx or dy:
                now = self.clock()
                accel = min(self.ACCEL_MAX,
                            1.0 + (now - self.t0) / self.ACCEL_S * (self.ACCEL_MAX - 1.0))
                s = self.BASE[self.level] * accel
                out = []
                for i, d in enumerate((dx, dy)):
                    v = self._frac[i] + d * s
                    out.append(int(v))
                    self._frac[i] = v - int(v)
                if out[0] or out[1]:
                    self.inp.move(*out)
                    self.trail.append((now, *out))
                while self.trail and now - self.trail[0][0] > self.TRAIL_S:
                    self.trail.popleft()


# ─────────────────────────── слежение за головой ────────────────────────────
class OneEuro:
    """Сглаживание, адаптивное к скорости: гасит дрожь в покое и почти не
    тормозит намеренный поворот. Обычный фильтр вынуждает выбирать одно из двух."""

    def __init__(self, min_cutoff=1.0, beta=0.01, dcutoff=1.0):
        self.min_cutoff, self.beta, self.dcutoff = min_cutoff, beta, dcutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.t is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(t - self.t, 1e-3)
        d = (x - self.x) / dt
        ad = self._a(self.dcutoff, dt)
        self.dx = ad * d + (1 - ad) * self.dx
        a = self._a(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


def head_angles(matrix):
    """Углы из вектора направления головы — третьего столбца матрицы поворота.

    Разложение на углы Эйлера через atan2 здесь непригодно: у некоторых поз оно
    неустойчиво и даёт скачки до 20° между соседними кадрами, то есть прыжок
    курсора через пол-экрана при неподвижной голове.
    """
    R = np.asarray(matrix).reshape(4, 4)[:3, :3]
    v = R[:, 2] / (np.linalg.norm(R[:, 2]) + 1e-9)
    return (math.degrees(math.atan2(v[0], abs(v[2]) + 1e-9)),
            math.degrees(math.asin(max(-1.0, min(1.0, -v[1])))))


def v4l2_default(dev, cid):
    """Значение элемента управления камеры по умолчанию — от драйвера.

    Вернуть «как было до нас» (cam.get перед установкой) не выходит: после
    аварийного выхода там уже наше 255, и его бы и «вернули»."""
    import fcntl, struct
    fmt = "II32siiiiI2I"               # struct v4l2_queryctrl
    VIDIOC_QUERYCTRL = 0xC0000000 | struct.calcsize(fmt) << 16 | ord("V") << 8 | 36
    buf = bytearray(struct.pack(fmt, cid, 0, b"", 0, 0, 0, 0, 0, 0, 0))
    with open(dev, "rb", buffering=0) as f:
        fcntl.ioctl(f, VIDIOC_QUERYCTRL, buf)
    return struct.unpack(fmt, buf)[6]


V4L2_CID_GAIN = 0x00980913


class Head(threading.Thread):
    """Курсор едет в ту сторону, куда повёрнута голова.

    Управление скоростное, а не позиционное: отклонение от нейтрали задаёт
    скорость движения. Поэтому до края экрана достаёт небольшой поворот и голову
    не нужно выворачивать — при больших углах модель теряет лицо.

    Пишет в то же устройство Input.ms, что и Mouse, поэтому движение от головы
    и от голосовых команд складывается само, без координации между режимами.
    """

    # Углы намеренно небольшие: камера часто стоит сбоку, и базовый поворот лица
    # к ней съедает запас — на 45-50° модель перестаёт уверенно видеть лицо.
    DEAD, DEAD_V = 2.2, 1.8        # мёртвая зона, ° — внутри неё курсор стоит
    SPAN, SPAN_V = 7.0, 5.0        # ещё столько ° сверх мёртвой зоны — до полной скорости
    VMAX, VMAX_V = 1000.0, 700.0   # полная скорость, px/с
    # Вниз от старта ступеней больше, чем вверх: самой медленной 0.40 для
    # точного наведения не хватало.
    LEVELS = (0.12, 0.22, 0.40, 0.65, 1.0)
    START = 3                      # 0.65
    PULL = 0.008                   # подтяжка нейтрали за кадр, пока голова в покое
    DRIFT_MAX = 4.0                # дальше этого нейтраль от исходной позы не уползает
    TICK = 1 / 60                  # как часто применяется скорость
    # Ручная выдержка даёт 23 FPS вместо 15 на автоэкспозиции. Единицы у
    # каждой камеры свои, поэтому значение — из [head] exposure config.toml.
    EXPOSURE = 400
    # Пока звучит речь, скорость курсора та, что была до неё. На «клик» при
    # неподвижной голове модель видела наклон на 3-4.4°, и курсор уезжал вверх
    # на 6-53 px — клик ложился мимо. Просто останавливать курсор нельзя:
    # ехавший курсор дёргается на каждой фразе. Хвост — на то, что кадр камеры
    # и фильтр отстают от звука.
    HUSH_S = 0.3
    HOLD_MAX_S = 1.5    # дольше — это уже не команда, а разговор: иначе курсор
                        # уехал бы на прежней скорости, хоть голову и вернули
    LOST_LIMIT_S = 30              # столько без лица — выключаемся, камера гаснет

    def __init__(self, inp, camera=0):
        super().__init__(daemon=True)
        self.inp = inp
        self.camera = camera
        self.level = self.START
        self.flag = threading.Event()
        self.landmarker = None
        self.vx = self.vy = 0.0
        self.hush_until = 0.0
        self._last_ts = 0        # метки времени для модели, общие на все сессии
        self._recenter = threading.Event()
        self.idle = threading.Event()  # камера закрыта и настройки возвращены
        self.idle.set()
        threading.Thread(target=self._drive, daemon=True).start()

    @property
    def active(self):
        return self.flag.is_set()

    def on(self):
        if not self.flag.is_set():
            self.flag.set()
            feedback("ГОЛОВА", "слежение включено", head=True)

    def off(self, notify=True):
        """notify=False — при смене режима: значок сбросит сам Tray.mode."""
        if self.flag.is_set():
            self.flag.clear()
            self.vx = self.vy = 0.0
            if notify:
                feedback("ГОЛОВА", "слежение выключено", head=False)

    def recenter(self):
        """Считать текущую позу новым «прямо» — если нейтраль сбилась."""
        if self.flag.is_set():
            self._recenter.set()
            feedback("ГОЛОВА", "центр переопределён", head=True)

    def hush(self):
        """Звучит речь: держать прежнюю скорость, см. HUSH_S."""
        self.hush_until = time.time() + self.HUSH_S

    def faster(self):
        self.level = min(len(self.LEVELS) - 1, self.level + 1)

    def slower(self):
        self.level = max(0, self.level - 1)

    @classmethod
    def _speed(cls, angle, dead, span, vmax):
        """Квадратичная характеристика: у нейтрали курсор ползёт для точного
        наведения, к краю диапазона разгоняется для перелётов."""
        a = abs(angle)
        if a <= dead:
            return 0.0
        t = min((a - dead) / span, 1.0)
        return math.copysign(vmax * t * t, angle)

    def _drive(self):
        """Применение скорости на 60 Гц, отдельно от 23 кадров камеры.

        Двигать курсор в такт кадрам значило бы 23 толчка в секунду. Дробные
        пиксели копятся: на скорости 8 px/с шаг за такт равен 0.13 px, и без
        накопителя округление съело бы движение целиком.
        """
        acc_x = acc_y = 0.0
        held = (0.0, 0.0)       # скорость до начала речи, см. HUSH_S
        held_since = 0.0
        last = time.time()
        while True:
            if not self.flag.is_set():
                # Слежение выключено: спим до «голова», а не тикаем впустую.
                # Время сна в dt не идёт, иначе первый шаг был бы рывком.
                self.flag.wait()
                last = time.time()
            time.sleep(self.TICK)
            now = time.time()
            dt, last = now - last, now
            if now < self.hush_until and now - held_since < self.HOLD_MAX_S:
                vx, vy = held
            elif now < self.hush_until:
                vx, vy = self.vx, self.vy
            else:
                vx, vy = held = self.vx, self.vy
                held_since = now
            if not self.flag.is_set() or (not vx and not vy):
                acc_x = acc_y = 0.0
                continue
            acc_x += vx * dt
            acc_y += vy * dt
            ix, iy = int(acc_x), int(acc_y)
            if ix or iy:
                acc_x -= ix
                acc_y -= iy
                self.inp.move(ix, iy)

    def _stamp(self):
        """Метка кадра для модели: строго возрастающая за всё время жизни процесса.

        Модель кэшируется между включениями, а detect_for_video отвергает метку
        не больше предыдущей. Время от начала сессии здесь не годится — при втором
        включении оно пошло бы с нуля, и модель сочла бы это прыжком в прошлое.
        """
        ts = int(time.monotonic() * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1      # два кадра в одну миллисекунду
        self._last_ts = ts
        return ts

    def _ensure_model(self):
        """Модель грузится один раз и живёт до конца процесса: повторное
        включение должно быть мгновенным. Камера, в отличие от неё, освобождается."""
        if self.landmarker is not None:
            return
        from mediapipe.tasks.python import BaseOptions, vision
        path = os.path.join(MODELS, "face_landmarker.task")
        if not os.path.exists(path):
            raise RuntimeError("нет face_landmarker.task в " + MODELS)
        self.landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=path),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                # Пороги ниже умолчания (0.5): лицо под большим углом к камере
                # распознаётся менее уверенно, а терять его нельзя — движение
                # обрывается на полпути.
                min_face_detection_confidence=0.3,
                min_face_presence_confidence=0.3,
                min_tracking_confidence=0.3,
                output_facial_transformation_matrixes=True))
        log("голова: модель загружена")

    def _open(self, cv2):
        """Камера из конфига; не открылся номер — первая из /dev/v4l/by-id.

        Номер /dev/videoN раздаётся по порядку подключения: после
        переподключения камеры «0» пропадает или уходит другому устройству
        (в журнале трижды «камера 0 занята или недоступна»). Путь by-id от
        порядка не зависит. Берём только index0: у UVC-камеры index1 — узел
        метаданных, кадров он не даёт. Путь из конфига не подменяем — его
        задали нарочно, и чужая камера вместо него хуже ошибки.
        """
        tried = [self.camera]
        if not isinstance(self.camera, str):
            tried += sorted(glob.glob("/dev/v4l/by-id/*index0"))
        for dev in tried:
            # V4L2 явно: путь вроде "/dev/video0" иначе открывается через
            # FFMPEG, а там выдержку и усиление не выставить.
            cam = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            if cam.isOpened():
                if dev != self.camera:
                    log("голова: камера", self.camera, "не открылась, взята", dev)
                return cam, dev
            cam.release()
        raise RuntimeError("камера занята или недоступна: "
                           + ", ".join(map(str, tried)))

    def _session(self):
        import cv2
        from mediapipe import Image, ImageFormat
        self._ensure_model()
        cam, dev = self._open(cv2)
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cam.set(cv2.CAP_PROP_FPS, 30)
        # Короткая выдержка плюс максимальное усиление сенсора. Автоэкспозиция при
        # нехватке света удлиняет выдержку и запирает частоту на 15 FPS; кадр от
        # усиления зернистее, но модели это безразлично, а частота решает плавность.
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        cam.set(cv2.CAP_PROP_EXPOSURE, self.EXPOSURE)
        cam.set(cv2.CAP_PROP_GAIN, 255)

        fy, fp = OneEuro(), OneEuro()
        neutral = anchor = None
        frames = lost = 0
        t0 = last_face = last_report = time.time()
        log("голова: слежение включено")
        try:
            while self.flag.is_set():
                ok, frame = cam.read()
                if not ok:
                    raise RuntimeError("камера перестала отдавать кадры")
                frames += 1
                res = self.landmarker.detect_for_video(
                    Image(image_format=ImageFormat.SRGB,
                          data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                    self._stamp())
                if not res.facial_transformation_matrixes:
                    # В скоростном режиме молчание опаснее, чем в позиционном:
                    # без обнуления курсор продолжит ехать на последней скорости.
                    self.vx = self.vy = 0.0
                    lost += 1
                    if time.time() - last_face > self.LOST_LIMIT_S:
                        log("голова: лица нет дольше", self.LOST_LIMIT_S, "с")
                        self.off()
                        break
                    continue
                last_face = now = time.time()
                y, p = head_angles(res.facial_transformation_matrixes[0])
                y, p = fy(y, now), fp(p, now)
                if neutral is None or self._recenter.is_set():
                    neutral = anchor = (y, p)
                    self._recenter.clear()
                    self.vx = self.vy = 0.0
                    continue
                ax, ay = y - neutral[0], p - neutral[1]
                if abs(ax) < self.DEAD and abs(ay) < self.DEAD_V:
                    # Нейтраль медленно едет за позой: человек оседает в кресле, и
                    # без подтяжки курсор через минуту поехал бы сам собой. Только
                    # в покое — иначе гасилось бы намеренное удержание поворота.
                    # Предел обязателен: уехав далеко, нейтраль делает движение в
                    # свою сторону недостижимым — туда пришлось бы выворачивать голову
                    # на угол, где модель уже теряет лицо, и направление «отказывает».
                    neutral = (
                        min(max(neutral[0] + ax * self.PULL,
                                anchor[0] - self.DRIFT_MAX), anchor[0] + self.DRIFT_MAX),
                        min(max(neutral[1] + ay * self.PULL,
                                anchor[1] - self.DRIFT_MAX), anchor[1] + self.DRIFT_MAX))
                if now - last_report > 5.0:
                    last_report = now
                    log(f"голова: поза=({y:+.1f},{p:+.1f}) нейтраль=({neutral[0]:+.1f},"
                        f"{neutral[1]:+.1f}) увод=({neutral[0]-anchor[0]:+.1f},"
                        f"{neutral[1]-anchor[1]:+.1f}) без лица {100*lost/max(frames,1):.0f}%")
                k = self.LEVELS[self.level]
                self.vx = -self._speed(ax, self.DEAD, self.SPAN, self.VMAX) * k
                self.vy = self._speed(ay, self.DEAD_V, self.SPAN_V, self.VMAX_V) * k
        finally:
            self.vx = self.vy = 0.0
            # Настройки камеры живут в драйвере и переживают процесс: не вернув
            # авторежим и усиление, оставим всем остальным приложениям тёмный
            # или зашумлённый кадр. Усиление — раньше автоэкспозиции: в ней
            # часть камер ведёт его сама и запись молча игнорирует.
            if not isinstance(dev, str):
                dev = f"/dev/video{dev}"
            try:
                cam.set(cv2.CAP_PROP_GAIN, v4l2_default(dev, V4L2_CID_GAIN))
            except OSError as ex:
                log("голова: усиление камеры не возвращено —", ex)
            cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
            cam.read()
            cam.release()
            el = max(time.time() - t0, 0.01)
            log(f"голова: выключено, {frames / el:.1f} FPS")

    def run(self):
        while True:
            self.flag.wait()
            self.idle.clear()
            try:
                self._session()
            except Exception as ex:
                log("голова: сбой —", ex)
                feedback("ГОЛОВА", f"не вышло: {ex}", head=False)
            finally:
                self.vx = self.vy = 0.0
                self.flag.clear()
                self.idle.set()

    def stop(self, timeout=2.0):
        """Перед выходом процесса: поток демонический, и без ожидания его
        finally с возвратом настроек камеры не успел бы выполниться."""
        self.flag.clear()
        self.idle.wait(timeout)


# ───────────────────────────────── команды ──────────────────────────────────
K = E
def combo(*names):
    return ("key", [getattr(K, n) for n in names])

# Прокрутка «на полэкрана» в щелчках колеса. Точного числа не существует:
# щелчок — это три «строки», а строка от ~18 px в GTK до ~33 px в Chrome,
# поэтому половина экрана (540 px при 1080p) — это от 5 до 10 щелчков.
# Семь — середина разброса; если промахивается, править здесь.
HALF_SCREEN = 7

COMMANDS = {
    # мышь
    "мышь влево":      ("move", (-1, 0)),
    "мышь вправо":     ("move", (1, 0)),
    "мышь вверх":      ("move", (0, -1)),
    "мышь вниз":       ("move", (0, 1)),
    "мышь левее":      ("nudge", (-1, 0)),
    "мышь правее":     ("nudge", (1, 0)),
    "мышь выше":       ("nudge", (0, -1)),
    "мышь ниже":       ("nudge", (0, 1)),
    "мышь сетка":      ("grid", None),
    "мышь координаты": ("grid", None),
    "стоп":            ("stop", None),
    "быстрее":         ("faster", None),
    "медленнее":       ("slower", None),
    # голова
    "голова":          ("head", True),
    # «следи» убран: грамматика рождала его из шипения и шума, даже когда
    # перед камерой никого не было
    "замри":           ("head", False),
    "центр":           ("recenter", None),
    "ровно":           ("recenter", None),
    "клик":            ("click", "left"),
    "щелчок":          ("click", "left"),
    "правый клик":     ("click", "right"),
    "средний клик":    ("click", "middle"),
    "двойной клик":    ("click", "double"),
    "зажать":          ("hold", True),
    "отпустить":       ("hold", False),
    "колесо вверх":    ("wheel", 3),
    "колесо вниз":     ("wheel", -3),
    # Читать страницу дальше — полэкрана за раз. Синонима «вперёд» быть не
    # может: в словаре vosk это слово с «ё», а norm() её стирает, поэтому
    # фраза выпала бы из грамматики и молча не срабатывала.
    "дальше":          ("wheel", -HALF_SCREEN),
    "страница вверх":  combo("KEY_PAGEUP"),
    "страница вниз":   combo("KEY_PAGEDOWN"),
    # клавиши
    "ввод":            combo("KEY_ENTER"),
    "отмена":          combo("KEY_ESC"),
    "таб":             combo("KEY_TAB"),
    "пробел":          combo("KEY_SPACE"),
    "стереть":         combo("KEY_BACKSPACE"),
    "удалить":         combo("KEY_DELETE"),
    "стрелка вверх":   combo("KEY_UP"),
    "стрелка вниз":    combo("KEY_DOWN"),
    "стрелка влево":   combo("KEY_LEFT"),
    "стрелка вправо":  combo("KEY_RIGHT"),
    "домой":           combo("KEY_HOME"),
    "конец строки":    combo("KEY_END"),
    # сочетания
    "копировать":      combo("KEY_LEFTCTRL", "KEY_C"),
    "вставить":        combo("KEY_LEFTCTRL", "KEY_V"),
    "вырезать":        combo("KEY_LEFTCTRL", "KEY_X"),
    "отменить":        combo("KEY_LEFTCTRL", "KEY_Z"),
    "сохранить":       combo("KEY_LEFTCTRL", "KEY_S"),
    "найти":           combo("KEY_LEFTCTRL", "KEY_F"),
    "выделить всё":    combo("KEY_LEFTCTRL", "KEY_A"),
    # Alt+← — назад по истории: браузер, файловый менеджер, справка
    "назад":           combo("KEY_LEFTALT", "KEY_LEFT"),
    # Alt+F10 — переключатель: сказанное второй раз вернёт окно в прежний размер.
    # Отдельной развёртки нет: maximize/unmaximize в GNOME 50 не назначены.
    "развернуть":      combo("KEY_LEFTALT", "KEY_F10"),
    "свернуть":        combo("KEY_LEFTMETA", "KEY_H"),
    "переключить окно": combo("KEY_LEFTALT", "KEY_TAB"),
    "обзор":           combo("KEY_LEFTMETA"),
    # Print — интерфейс скриншота GNOME: область выбирают мышью или головой,
    # «клик» по кнопке затвора снимает. KEY_SYSRQ, а не KEY_PRINT: именно код 99
    # xkb превращает в Print, у KEY_PRINT (210) другое назначение.
    "скриншот":        combo("KEY_SYSRQ"),
    "закрыть окно":    combo("KEY_LEFTALT", "KEY_F4"),
    # Claude Code в терминале
    "отправить":       combo("KEY_ENTER"),
    "прервать":        combo("KEY_ESC"),
    "новая строка":    combo("KEY_LEFTCTRL", "KEY_J"),
    "режим план":      combo("KEY_LEFTSHIFT", "KEY_TAB"),
    "очистить экран":  combo("KEY_LEFTCTRL", "KEY_L"),
    "история":         combo("KEY_UP"),
    "вариант один":    combo("KEY_1"),
    "вариант два":     combo("KEY_2"),
    "вариант три":     combo("KEY_3"),
    # режимы. Язык диктовки — не отдельное состояние, а параметр: сброс буфера,
    # глушение мыши и смена грамматики для обоих языков одинаковы.
    "печатать":        ("dictate", "ru"),
    "писать":          ("dictate", "ru"),
    # «русский» убран: обычное слово разговора, дважды за день (0.90 и 0.81)
    # включало диктовку посреди беседы — и она вставляла её в окно
    "английский":      ("dictate", "en"),
    "консоль":         ("shell", None),
    "сон":             ("mode", SLEEP),
}
LANG_NAME = {"ru": "русский", "en": "английский"}
WAKE = {"проснись": ("mode", COMMAND)}
# Приманки СНА. Барьеров во сне нет, а грамматике из одной фразы любое слово
# на «про-» ближе всего к «проснись»: на синтезе «просто», «прошу», «пусть»,
# «он просто так сказал» -> «проснись» с уверенностью 1.00, ложных
# пробуждений 32 из 42 похожих фраз. С приманками — 1 из 42, и 2 из 27 на
# фразах, по которым их не подбирали (было 14). Само «проснись», в том числе
# после чужих слов, они не отбирают.
WAKE_DECOYS = ["просто", "прости", "прошу", "проверь", "пусть", "проснулся",
               "приснилось", "понеслась", "после", "против"]
EXIT_DICTATION = ["конец текста", "хватит печатать"]
SHELL_EXIT = ["хватит консоль", "конец текста"]
SHELL_RUN = "ввод"      # Enter: вставленная команда выполняется только по нему
WHISPER_RUN = {"ввод", "вот", "вод"}    # так whisper пишет «ввод»
SHELL_UNDO = "нет"      # стереть последнюю вставку, пока она не выполнена
# Приманки. У грамматики КОНСОЛИ всего четыре фразы, и «claude» и «code» ей
# ближе всего к «ввод»: на синтезе «клод» -> «ввод» с уверенностью 1.00, то
# есть Enter вместо слова. С приманками «клод», «клауд», «клоуд», «код»
# притягиваются к ним, а сама фраза остаётся в буфере и уходит в whisper.
# «Вот» в приманки не годится: он отбирает и настоящий «ввод».
# «Там» — ради «таб»: без приманки «посмотри что там влево» грамматика
# складывала в «по словам влево», и арбитр пропускал.
SHELL_DECOYS = ["клод", "код", "там"]
# Терминалы GNOME (Ptyxis, gnome-terminal) Ctrl+V не вставляют: это сочетание
# уходит в программу как символ ^V.
TERMINAL_PASTE = (E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_V)
# Клавиши строки: история, курсор, Esc. Ctrl+←/→ — по словам: так их
# понимает readline (/etc/inputrc) и Claude Code.
SHELL_KEYS = {
    "вверх":            [E.KEY_UP],
    "вниз":             [E.KEY_DOWN],
    "влево":            [E.KEY_LEFT],
    "вправо":           [E.KEY_RIGHT],
    "по словам влево":  [E.KEY_LEFTCTRL, E.KEY_LEFT],
    "по словам вправо": [E.KEY_LEFTCTRL, E.KEY_RIGHT],
    # «эск» был, но с числами в грамматике он проигрывал «раз»
    "эскейп":           [E.KEY_ESC],
    # дописать имя: «сиди проджектс слэш» и «таб»
    "таб":              [E.KEY_TAB],
    "домой":            [E.KEY_HOME],
    "конец строки":     [E.KEY_END],
    # те же фразы, что в КОМАНДАХ; «стереть» и «удалить» — ещё и по уверенности
    # (DESTRUCTIVE)
    "страница вверх":   [E.KEY_PAGEUP],
    "страница вниз":    [E.KEY_PAGEDOWN],
    "стереть":          [E.KEY_BACKSPACE],
    "удалить":          [E.KEY_DELETE],
    # В терминале Ctrl+C прерывает, а Ctrl+V уходит в программу символом ^V —
    # копирует и вставляет здесь Ctrl+Shift. В КОМАНДАХ так нельзя: Wayland не
    # говорит, какое окно в фокусе, а в браузере Ctrl+Shift+C — инспектор.
    "копировать":       [E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_C],
    "вставить":         list(TERMINAL_PASTE),
    **{f"эф {w}": [getattr(E, f"KEY_F{n}")] for n, w in enumerate(
        ("один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь",
         "девять", "десять"), 1)},
}
SHELL_STRICT_KEYS = {"вставить"}
# «Буква …» — латинская буква мимо whisper: ответ «y» на [y/N] и имена по
# буквам. Одиночное «уай» whisper и LLM разбирают как попало, а здесь фраза
# из грамматики. Слово «буква» впереди — чтобы «ай», «и», «а» из речи не
# печатали букв. Кроме английских названий (shell.LETTERS) — школьные:
# «игрек», «зет», «аш». «Йот» нет в словаре vosk. «У» и «жи» убраны: на
# синтезе «у» путалась с «оу» (u против o), «жи» слышалась как «джи» (g).
LATIN_NAMES = {
    "а": "a", "бэ": "b", "цэ": "c", "дэ": "d", "е": "e", "эф": "f", "гэ": "g",
    "жэ": "g", "аш": "h", "и": "i", "ка": "k", "эль": "l", "эм": "m",
    "эн": "n", "о": "o", "пэ": "p", "ку": "q", "эр": "r", "эс": "s", "тэ": "t",
    "вэ": "v", "дубль вэ": "w", "икс": "x", "игрек": "y", "зет": "z",
}
SHELL_LETTERS = {f"буква {k}": v for k, v in {**shell.LETTERS, **LATIN_NAMES}.items()}
# «Контрол …» — Ctrl+буква. Названия букв те же, что у «буква …», — какое ни
# скажи. Только «це» в грамматику не входит: оно звучит как «цэ», vosk делит
# уверенность между двойниками поровну, и «контрол цэ» получал 0.75 —
# строгий порог MIN_CONF его не пропускал (журнал 28.09, 13:53, одиннадцать
# отказов подряд). Без двойника сказанное «контрол це» слышится как
# «контрол цэ» с уверенностью 1.0. «Эл» и «эль» — тоже двойники, но Ctrl+L
# не строгий, а без «контрол эл» vosk всё равно отдаёт его: слово есть в
# «буква эл».
# Ctrl+R (поиск по истории) и Ctrl+L (очистить экран) безвредны; остальные
# прерывают (C, Z), закрывают ввод или оболочку (D) и стирают строку
# (U, K, W) — они строгие, как «ввод» (SHELL_STRICT_KEYS).
CTRL_KEYS = {f"контрол {k}": [E.KEY_LEFTCTRL, getattr(E, f"KEY_{v.upper()}")]
             for k, v in {**shell.LETTERS, **LATIN_NAMES}.items()
             if v in "cdzukwrl" and k != "це"}
SHELL_KEYS.update(CTRL_KEYS)
SHELL_STRICT_KEYS |= {k for k, v in CTRL_KEYS.items()
                      if v[1] not in (E.KEY_R, E.KEY_L)}
# После них курсор в начале пустого ввода: новая строка, поиск или то, что
# осталось правее курсора
CTRL_EMPTIES_LINE = [[E.KEY_LEFTCTRL, c] for c in (E.KEY_C, E.KEY_Z, E.KEY_U, E.KEY_R)]
# Повторы: «влево пять раз». Только для перемещения — Esc и Home повторять
# незачем. Числа до десяти: каждое — ещё фразы в грамматике, а дальше можно
# двумя фразами подряд. «Два раза» говорят чаще, чем «два раз», — берём оба.
REPEATABLE = ["вверх", "вниз", "влево", "вправо", "по словам влево", "по словам вправо",
              "страница вверх", "страница вниз", "стереть", "удалить"]
NUMBERS = {"два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
           "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
REPEAT_PAUSE_S = 0.03   # между нажатиями: иначе терминал теряет часть из них
# В КОМАНДАХ — курсор, стирание и колесо. Каждая команда — ещё двенадцать
# фраз в грамматике и столько же приманок для чужой речи, поэтому не все.
# Цена — полсекунды: у «стереть» есть продолжение «… пять раз», и vosk ставит
# конец фразы не через 0.5 с тишины, а через 1.0 с (синтез: 1.33 с против
# 0.83). Автомат грамматики сливает общие хвосты, поэтому ждут и соседи по
# последнему слову: «мышь влево» 1.49 против 0.74, «вариант два» 1.44 против
# 0.94. Число впереди («пять раз стереть») задержки не даёт, но порядок был
# бы не тот, что в КОНСОЛИ.
COMMAND_REPEATABLE = ["стрелка вверх", "стрелка вниз", "стрелка влево", "стрелка вправо",
                      "стереть", "удалить", "колесо вверх", "колесо вниз"]


def repeats(phrase):
    """«стереть два раза» … «стереть десять раз» -> сколько раз."""
    return {f"{phrase} {word} {times}": n for word, n in NUMBERS.items()
            for times in (("раз", "раза") if n <= 4 else ("раз",))}


def norm(s):
    s = s.lower().replace("ё", "е").strip()
    return re.sub(r"\s+", " ", re.sub(r"[^\wа-яА-Я ]", "", s))


def whisper_says_run(text):
    """Повторённый «ввод» whisper пишет одной строкой — «вот, вот». Сравнивать
    её целиком с WHISPER_RUN нельзя: она в набор не входит, и уходила в LLM."""
    words = norm(text).split()
    return bool(words) and all(w in WHISPER_RUN for w in words)

NORM_COMMANDS = {norm(k): v for k, v in COMMANDS.items()}
NORM_COMMANDS.update({norm(p): ("times", (COMMANDS[k], n))
                      for k in COMMAND_REPEATABLE for p, n in repeats(k).items()})
NORM_WAKE = {norm(k): v for k, v in WAKE.items()}
NORM_EXIT = [norm(x) for x in EXIT_DICTATION]
NORM_SHELL_EXIT = [norm(x) for x in SHELL_EXIT]
NORM_RUN = norm(SHELL_RUN)
NORM_UNDO = norm(SHELL_UNDO)
NORM_STOP = norm("стоп")
# Номера — только в СЕТКЕ: в КОМАНДАХ двести фраз стали бы двумястами
# приманок для чужой речи. «Сон» в СЕТКУ не входит по той же причине:
# «нет», а за ним «сон» дают то же самое.
NORM_GRID = {norm(k): v for k, v in grid.NUMBERS.items()}
NORM_GRID_CLOSE = [norm("нет"), norm("стоп")]
# после первого номера: щелчок в центр клетки; фраза -> (кнопка, двойной)
NORM_GRID_CLICK = {norm("клик"): (E.BTN_LEFT, False), norm("правый клик"): (E.BTN_RIGHT, False),
                   norm("двойной клик"): (E.BTN_LEFT, True)}
# «ввод» и «нет» меняют строку терминала, а «нет» ещё и частое слово речи —
# их принимают только отдельной фразой, строже прочих (см. Ears._judge)
STRICT = {NORM_RUN, NORM_UNDO} | {norm(k) for k in SHELL_STRICT_KEYS}
# фраза -> (сочетание, сколько раз)
NORM_SHELL_KEYS = {norm(k): (v, 1) for k, v in SHELL_KEYS.items()}
NORM_SHELL_KEYS.update({norm(p): (SHELL_KEYS[k], n)
                        for k in REPEATABLE for p, n in repeats(k).items()})
# вместо сочетания — сама буква: её вставляют, а не нажимают
NORM_SHELL_KEYS.update({norm(k): (v, 1) for k, v in SHELL_LETTERS.items()})
# Эти команды уничтожают работу, поэтому принимаются только по финальному
# результату vosk с проверкой уверенности — лишние ~300 мс того стоят.
# «Голова» — не разрушает, но грамматика рождает её из шипения и шума, а
# барьер по словам одиночный звук не ловит: слов рядом нет.
DESTRUCTIVE = {norm(x) for x in
               ("стереть", "удалить", "вырезать", "закрыть окно", "отменить",
                "голова")}
# «стереть пять раз» — в обоих режимах
DESTRUCTIVE |= {k for k in [*NORM_SHELL_KEYS, *NORM_COMMANDS] if k.split()[0] in DESTRUCTIVE}
MIN_CONF = 0.80
# «Консоль» отдаёт речь whisper: ложное срабатывание молча превращает «клик»
# в набранный текст. Нарочно сказанная — уверенность 1.00 в 41 случае из 43,
# ложная (27.09, посреди кликов) — 0.84, и MIN_CONF её пропустил.
# «Пробел» не безвреден: листает страницу, ставит плеер на паузу. Из 12
# срабатываний нарочных было 4, все 1.00; ложные, посреди разговора, —
# 0.43–0.88, и MIN_CONF пропустил бы 0.84 и 0.88. В КОНСОЛИ порог тот же.
HIGH_CONF = {norm("консоль"): 0.95, norm("пробел"): 0.95}
# «Таб» в КОМАНДАХ уводит фокус в браузере и формах. Из 14 срабатываний
# нарочной была одна серия — 0.86–0.93 (и 1.00); одиночные посреди разговора
# — 0.46–0.83, а 07.10 «стоп» во время езды мыши услышан как «таб» (0.68).
# В КОНСОЛИ «таб» — дополнение, ложный дёшев, а нарочный бывал и 0.83.
COMMAND_CONF = {norm("таб"): 0.85}


def need_conf(t, state):
    """Какая уверенность vosk нужна фразе t в режиме state."""
    need = HIGH_CONF.get(t, MIN_CONF if t in DESTRUCTIVE else 0.0)
    return max(need, COMMAND_CONF.get(t, 0.0)) if state == COMMAND else need
# Команды, которым барьеры Ears не нужны: ложное срабатывание дёшево
# («проснись»), а невыполненное — делает систему неуправляемой. На живой
# речи «сон» отклонялся через раз: соседний звук давал арбитру 3–5 слов.
UNGUARDED = {norm(x) for x in ("сон",)}
# whisper часто успевает захватить лишь обрывок команды выхода («конец»),
# поэтому чистим хвост по отдельным словам, а не по фразе целиком
# Длинные слова — по основе: vosk засчитал выход, а whisper написал «конец
# консоли», и LLM сделала из обрывка exit. Короткие («нет», «ввод») — только
# целиком, иначе съели бы «нечто» в конце команды.
# Клавишные фразы — только целиком: по словам «эф один» и «вверх пять раз»
# срезали бы числа, и «сто девяносто один» вставлялось как 190.
def tail_re(phrases, whole=()):
    words = sorted({w for p in phrases for w in p.split()}, key=len, reverse=True)
    alts = [w[:-1] + r"\w*" if len(w) >= 6 else w for w in words]
    alts = sorted((r"\s+".join(p.split()) for p in whole), key=len, reverse=True) + alts
    return re.compile(r"[\s,.!?…-]*\b(?:" + "|".join(alts) + r")\b[\s,.!?…-]*$", re.I)


def strip_tail(text, heard, in_shell):
    """Срезать с конца расшифровки обрывки фраз, которые vosk в ней слышал.

    Срезать всё, что похоже на выход или клавишу, нельзя: «эхо нет» теряло
    «нет», «cat текст» — «текст». Только по засчитанной фразе — тоже: whisper
    бывает готов раньше vosk, а отклонённый барьером «ввод» всё равно остаётся
    в записи. Поэтому — по всему, что грамматика отдала в этом фрагменте.
    Срезается не больше слов, чем в самой фразе выхода: «пришли текст,
    конец текста» иначе теряло и надиктованный «текст».
    """
    def said(p):
        return any(f" {p} " in f" {h} " for h in heard)
    exit_all = NORM_SHELL_EXIT if in_shell else NORM_EXIT
    # какая из фраз выхода сказана, whisper не различает: «конец консоли»
    exits = exit_all if any(map(said, exit_all)) else []
    keys = [p for p in [*STRICT, *NORM_SHELL_KEYS] if said(p)] if in_shell else []
    if not exits and not keys:
        return text
    rx = tail_re(exits, keys)
    for _ in range(max((len(p.split()) for p in exits), default=0) + bool(keys)):
        new = rx.sub("", text).strip()
        if new == text:
            break
        text = new
    return text


# ─────────────────────────── обратная связь о режиме ─────────────────────────
TRAY = None     # значок в трее; ставит Daemon, если трей поднялся


def play(sound):
    """Звук режима (0,06–1 с) после тишины WAKE_PAD_S — иначе спящий цифровой
    выход его проглатывает. Тишина — отдельным pw-play, чтобы не разбирать .oga."""
    path = os.path.join(SOUNDS, sound + ".oga")
    if not os.path.exists(path):
        return

    def run():
        quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pw-play", "--raw", "--rate", "48000", "--channels", "2",
                        "--format", "s16", "-"],
                       input=bytes(int(48000 * WAKE_PAD_S) * 4), **quiet)
        subprocess.run(["pw-play", path], **quiet)
    threading.Thread(target=run, daemon=True).start()


def feedback(state, extra="", lang=None, head=False):
    play({SLEEP: "dialog-warning", COMMAND: "message-new-instant",
          DICTATION: "message", SHELL: "dialog-information"}.get(state, "bell"))
    if TRAY is None:
        return
    if state == "ГОЛОВА":
        TRAY.head_note(extra, head)
    else:
        TRAY.mode(state, lang, extra)



# ──────────────────────── распознавание команд ──────────────────────────────
def grammar_for(state):
    """Фразы, которые движок вправе услышать в этом режиме."""
    if state == SLEEP:
        phrases = list(NORM_WAKE) + WAKE_DECOYS
    elif state == DICTATION:
        phrases = NORM_EXIT
    elif state == GRID:
        phrases = list(NORM_GRID) + NORM_GRID_CLOSE + list(NORM_GRID_CLICK)
    elif state == SHELL:
        # «вставить» и строгие «контрол …» есть и в STRICT, и в NORM_SHELL_KEYS:
        # на уверенность дубль не влияет (замер 15.10: 1.0 в обоих случаях),
        # но и грамматике он ни к чему
        phrases = list(dict.fromkeys(NORM_SHELL_EXIT + sorted(STRICT)
                                     + sorted(NORM_SHELL_KEYS) + SHELL_DECOYS))
    else:
        phrases = list(NORM_COMMANDS) + NORM_EXIT
    return json.dumps(phrases + ["[unk]"], ensure_ascii=False)


def vocab_check(model):
    """Все слова грамматик -> (слова, каких нет в словаре vosk).

    vosk не падает на незнакомом слове, а пишет в stderr и выбрасывает его
    из грамматики — поэтому ловим его строку, подставляя stderr на время.
    Её зовёт и test_commands.py: при правке таблиц журнал демона никто не
    читает.
    """
    import tempfile, vosk
    phrases = (list(NORM_COMMANDS) + list(NORM_WAKE) + WAKE_DECOYS + NORM_EXIT
               + NORM_SHELL_EXIT + sorted(STRICT) + list(NORM_SHELL_KEYS)
               + SHELL_DECOYS + list(NORM_GRID) + NORM_GRID_CLOSE + list(NORM_GRID_CLICK))
    words = sorted({w for p in phrases for w in p.split()})
    missing = []
    for w in words:
        with tempfile.TemporaryFile(mode="w+") as tf:
            old = os.dup(2); os.dup2(tf.fileno(), 2)
            try:
                vosk.KaldiRecognizer(model, RATE, json.dumps([w, "[unk]"], ensure_ascii=False))
            finally:
                os.dup2(old, 2); os.close(old)
            tf.seek(0)
            if "Ignoring word missing" in tf.read():
                missing.append(w)
    return words, missing


class Ears:
    """Два распознавателя на одном потоке: грамматика и арбитр.

    Грамматика из нескольких десятков фраз даёт точность на командах, но
    сказать «это вообще не команда» она не умеет: выбор идёт только между
    известными фразами, поэтому любую постороннюю речь она раскладывает на
    ближайшие знакомые слова. «Мы поговорим об этом» уверенно становится
    командой «домой» — с максимальной уверенностью 1.00, так что отсечь это
    порогом уверенности нельзя, проверено на записях.

    Поэтому рядом слушает второй распознаватель, со всем словарём. Совпадения
    текстов от него не ждём: на коротком слове модель легко слышит «стол»
    вместо «стоп» — ради этого грамматика и нужна. Он отвечает на другой
    вопрос: СКОЛЬКО было сказано. Команду произносят отдельной короткой
    фразой, а ложное срабатывание рождается внутри потока речи, где арбитр
    насчитывает много слов. Этого хватает, чтобы их различить.
    """
    MAX_EXTRA_WORDS = 1     # арбитр вправе услышать на слово больше, чем в команде
    PAUSE_S = 0.3           # столько тишины разделяет две фразы
    # Сколько может звучать фраза, чтобы сойти за команду. Прежние 1.2 с
    # подбирались, когда длительность была суммой блоков громче порога, а сам
    # порог стоял выше живого голоса: метрика занижала звучание в разы, и
    # барьер почти не срабатывал. Теперь она честная — время от начала фразы
    # до конца, — поэтому и запас взят по живой речи, а не по синтезатору.
    SPEECH_S = 2.0          # на первое слово
    SPEECH_S_MORE = 0.7     # на каждое следующее
    # «Ввод» жмёт Enter, поэтому его судят строже: ни одного лишнего слова и
    # не дольше секунды. Обычный запас пропускал его из середины фраз — у
    # «надо сделать ввод» арбитр насчитал 2 слова за 1.25 с, а отдельно
    # сказанный «ввод» дал 0 слов и 0.5 с.
    RUN_S = 1.0
    # Но одно слово — 2.0 с: живой «ввод», сказанный нарочно, длился 1.1–1.9 с
    # (журнал 28.09: почти все отказы по длительности, whisper в тех же слышал
    # одно «вот»), а повторяют его громче и протяжнее. Середину фраз для
    # одного слова отбивает барьер по словам: «надо сделать ввод» — 2 слова
    # при допустимом одном. Двухсловным предел не поднят: «контрол цэ» из
    # «в контрол цэ нажми» ловит только длительность.
    RUN_S_ONE = 2.0
    # Двухсловной фразе — 1.4 с: «контрол си» на синтезе длится 1.38 с, а
    # «контрол цэ» из «в контрол цэ нажми» — 1.5 с, и отбивает его только
    # длительность: лишних слов арбитр там не насчитал. «Контрол дубль вэ» —
    # 1.5 с при пределе 1.8.
    RUN_S_MORE = 0.4
    # Повтор строгой фразы раньше этого — тот же «ввод», сказанный ещё раз,
    # потому что первый не успел сработать: грамматика засчитывает его почти
    # через секунду. Повторы на синтезе приходят через 0.5–1.1 с, а нарочно
    # сказанные подряд «ввод» в журнале — не чаще раза в 2 с.
    REPEAT_S = 1.5
    # Сколько последних слов арбитра пишется в строку «отклонено». При
    # непрерывном звуке (телевизор, созвон) пауза поток не закрывает, и без
    # предела строка «рядом сказано 62 слов» уносила в журнал весь чужой
    # разговор. Команде из трёх слов и её окрестности хватает и двенадцати.
    HEARD_WORDS = 12

    def __init__(self, model, state, gate=200.0):
        import vosk
        self.vosk, self.model = vosk, model
        self.gate = gate            # уровень, выше которого блок считается речью
        self.free = vosk.KaldiRecognizer(model, RATE)
        self._closed = 0            # слова из уже закрытых сегментов арбитра
        self._run = 0               # слова в текущем непрерывном потоке речи
        self._last_run = 0          # слова в потоке, который только что кончился
        self._run_s = self._last_run_s = 0.0    # он же, но в секундах звучания
        self._silence = 0.0
        self.listen(state)

    def listen(self, state):
        """Сменить грамматику под режим."""
        try:
            self.rec = self.vosk.KaldiRecognizer(self.model, RATE, grammar_for(state))
        except Exception as ex:
            log("грамматика отклонена, свободное распознавание:", ex)
            self.rec = self.vosk.KaldiRecognizer(self.model, RATE)
        self.rec.SetWords(True)
        # Барьеры нужны только в режиме команд: там 64 фразы, и посторонняя
        # речь неизбежно во что-нибудь из них попадает. В диктовке фраз всего
        # две, во сне одна, а цена ошибки обратная — лишний выход из диктовки
        # почти безвреден, тогда как невозможность выйти или проснуться делает
        # систему неуправляемой. «Конец текста» и говорят-то обычно без паузы,
        # сразу после надиктованного.
        self.guard = state not in (SLEEP, DICTATION, SHELL)
        self.state_shell = state == SHELL
        # Фразы режима, которые могут идти хвостом за посторонней речью.
        # «Ввод» к ним не относится, у него свой путь в feed().
        self._tails = [p for p in json.loads(grammar_for(state))
                       if p != "[unk]" and p not in STRICT and p not in NORM_SHELL_KEYS]
        # Арбитр тоже начинает с чистого листа: на синтезе пауза успевает
        # сбросить его раньше, чем грамматика отдаёт «проснись», но живой
        # микрофон тише и шумнее, а «пробел» сразу после пробуждения дважды
        # отклонён как «сиди про бегстве» — с чужим словом впереди.
        self.free.Reset()
        self._closed = self._run = self._last_run = 0
        # Что слышал арбитр — только для строки «отклонено»: без текста по ней
        # не понять, чужая ли это речь или барьер ошибся.
        self._closed_text = self._run_text = self._last_text = self._last_now = ""
        self._hits, self._last_hits = {}, {}    # строгая фраза -> сколько раз в потоке
        self._fired = {}            # строгая фраза -> когда нажата или повторена
        self._t = 0.0               # время звука, с
        self._run_s = self._last_run_s = self._silence = 0.0
        self._pending = None        # (фраза, результат), ждущие конца потока
        # Начало последней принятой фразы на часах _t: метки слов vosk идут
        # от создания распознавателя, а он создаётся здесь же, с _t = 0.
        self.said_at = 0.0
        self.heard = set()          # что отдала грамматика с прошлой расшифровки whisper

    def tail_s(self):
        """Сколько звука с конца занимает только что принятая фраза.

        Фразу засчитывают посреди тишины, которая её закрыла, поэтому хвост —
        это сама фраза плюс натёкшая после неё тишина. Постоянный срез тут не
        годится: «ввод» засчитывается почти через секунду после конца слова, и
        срез в 0.8 с оставлял слово целиком — whisper вставлял «вот» вместо
        Enter. Запас — на тихое начало слова, которое ниже порога.
        """
        return self._last_run_s + self._silence + 0.25

    def since_word_s(self, share=0.0):
        """Сколько секунд назад была точка на доле share только что принятой фразы.

        share = 0 — начало фразы, 1 — её конец. Без запаса на тихое начало
        (его добавляет tail_s): здесь нужна точка, где курсор был, когда
        человек начал говорить.
        """
        return self._last_run_s * (1.0 - share) + self._silence

    def now(self):
        """Сколько звука прошло через уши с последней смены режима, с."""
        return self._t

    def partial(self):
        """Что грамматика слышит прямо сейчас, ещё не закрыв фразу."""
        return norm(json.loads(self.rec.PartialResult()).get("partial", ""))

    @staticmethod
    def _words(text):
        return len(text.split())

    def _tail(self, text):
        return " ".join(text.split()[-self.HEARD_WORDS:])

    def _heard_now(self):
        """Последняя гипотеза арбитра в открытом потоке."""
        return self._tail(self._closed_text + " " +
                          json.loads(self.free.PartialResult()).get("partial", ""))

    def feed(self, data):
        """Блок аудио -> (фраза, уверенность), если она прошла арбитра.

        Работаем только по финальному результату. Частичный давал реакцию
        быстрее, но на записях он ловил лишь одну настоящую команду из
        двенадцати, а посторонняя речь пробивала его почти всегда: гипотезы
        движка на лету проходят через короткие слова, и «печатать» внутри
        чужой фразы держится ровно столько же, сколько сказанное нарочно.
        """
        ready = None
        pcm = np.frombuffer(data, dtype=np.int16)
        loud = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) > self.gate
        if self.free.AcceptWaveform(data):
            closed = json.loads(self.free.Result()).get("text", "")
            self._closed += self._words(closed)
            self._closed_text = self._tail(self._closed_text + " " + closed)
        step = len(pcm) / RATE
        self._t += step
        if loud:
            self._silence = 0.0
            self._run_s += step
            # Максимум, а не текущее значение: арбитр на лету пересматривает
            # гипотезу, и «отпечаток на победу» посреди фразы схлопывается
            # в одно «медведя». Грамматика как раз в такой момент и выдаёт
            # «печатать» — без паузы, — и барьер видел одно слово вместо трёх.
            partial = json.loads(self.free.PartialResult()).get("partial", "")
            if self._closed + self._words(partial) > self._run:
                self._run_text = self._tail(self._closed_text + " " + partial)
            self._run = max(self._run, self._closed + self._words(partial))
        else:
            self._silence += step
            # Провал внутри фразы — тоже её длительность. Считать одни лишь
            # блоки громче порога нельзя: тогда то же самое слово, сказанное
            # громче, «звучит» дольше — придыхание и переходы между слогами
            # поднимаются над порогом и идут в зачёт. А команду, которую не
            # расслышали, повторяют именно громче, и барьер отвергает её тем
            # увереннее, чем настойчивее её произносят.
            if self._run_s:
                self._run_s += step
            # Пауза закрывает поток речи. Замер делаем именно здесь: грамматика
            # выдаёт фразу уже посреди тишины, и к тому моменту считать нечего.
            # Условие на _closed, а не только на _run_s: арбитр слышит и речь
            # тише порога, которую RMS не считает громкой. Без него такие
            # слова копятся без сброса минутами и выстреливают все разом при
            # первом же громком звуке — стуке, кашле, громком слоге. Партиал —
            # по той же причине: тихое слово, ещё не закрытое самим vosk,
            # иначе пережидает паузу и встаёт в начало следующей команды.
            if self._silence >= self.PAUSE_S and (
                    self._run_s or self._closed
                    or json.loads(self.free.PartialResult()).get("partial")):
                self._last_run, self._run, self._closed = self._run, 0, 0
                self._last_now = self._heard_now()     # до Reset: потом партиал пуст
                self._last_text, self._run_text, self._closed_text = self._run_text, "", ""
                self._last_hits, self._hits = self._hits, {}
                # Хвост тишины набежал уже после фразы: _silence обнуляется
                # на каждом громком блоке, поэтому он ровно столько же, сколько
                # успело натечь в _run_s после последнего слога.
                self._last_run_s = max(self._run_s - self._silence, 0.0)
                self._run_s = 0.0
                self.free.Reset()
                if self._pending:
                    ready, self._pending = self._judge(*self._pending), None
        if not self.rec.AcceptWaveform(data):
            return ready
        res = json.loads(self.rec.Result())
        text = norm(res.get("text", ""))
        if not text:
            return ready
        self.heard.add(text)
        # «Ввод» жмёт Enter, и «эхо ввод данных» выполнило бы недоговорённую
        # команду. Грамматике из трёх фраз чужие слова даже [unk] не стоят —
        # она отдаёт голое «ввод», — поэтому его, как команду, судят барьеры.
        # В длинной фразе vosk ставит [unk] на запинке: «по словам unk вправо
        # два раза». Внутри фразы из трёх слов и больше его выбрасываем — лишнюю
        # речь всё равно поймают барьеры. «unk вверх» — другое: это клавиша
        # вслед за чужой речью, и её не принимаем.
        if self.state_shell and "unk" in text.split():
            bare = " ".join(w for w in text.split() if w != "unk")
            if bare in NORM_SHELL_KEYS and len(bare.split()) >= 3 \
                    and not text.startswith("unk") and not text.endswith("unk"):
                text = bare
        # Повтор строгой фразы без паузы грамматика склеивает: «ввод ввод».
        # Это та же фраза, сказанная дважды, а не чужая речь (см. _judge).
        reps = 1
        for p in STRICT:
            k = len(text.split()) // len(p.split())
            if k > 1 and text == " ".join([p] * k):
                text, reps = p, k
        # Клавиши КОНСОЛИ — тоже команды, и «вверх» внутри речи не должен
        # листать историю: их судят барьеры, но с обычным, не строгим запасом.
        if not self.guard and text not in STRICT and text not in NORM_SHELL_KEYS:
            # «Так, подожди секунду, проснись» грамматика отдаёт как
            # «unk проснись»: чужие слова она сводит к [unk]. Без барьеров
            # важно лишь, чем фраза кончилась, — иначе выход из диктовки и
            # пробуждение не срабатывают как раз тогда, когда их говорят
            # вслед за другой речью. В режиме команд так делать нельзя:
            # там [unk] рядом с командой — признак посторонней речи.
            text = next((p for p in self._tails
                         if text == p or text.endswith(" " + p)), text)
            return self._accept(text, res)
        if text in UNGUARDED:
            return self._accept(text, res)
        # Грамматика бывает готова раньше, чем кончилась фраза: «печатать»
        # выходит на 1.6 с из 2.6 с «надо печатать документы на принтере».
        # В этот момент оба барьера видят лишь начало — два слова и полторы
        # секунды — и пропускают. Поэтому, пока поток речи открыт, решение
        # откладываем до паузы, которая его закроет: задержка не больше
        # PAUSE_S, и только когда фраза пришла посреди речи.
        if self._run_s:
            self._pending = (text, res, reps)
            return ready
        return self._judge(text, res, reps)

    def _judge(self, text, res, reps=1):
        """Вердикт барьеров по фразе, для которой поток речи уже измерен."""
        # Два независимых признака, и каждого достаточно для отказа. Арбитр
        # силён на долгой речи, но на короткой фразе он ещё молчит — там
        # выручает длительность: «надо печатать документы» звучит втрое
        # дольше, чем «печатать», сказанное нарочно.
        words = self._words(text)
        said = self._run or self._last_run
        strict = text in STRICT
        # Нерасслышанный «ввод» повторяют, пока первый ещё не засчитан, —
        # грамматика отдаёт оба уже после второго, и оба судились по одному
        # потоку: «2 слов при допустимых 1» дважды в одну секунду (журнал
        # 28.09, 16:51), а при паузе с отзвуком — два Enter. Повторы — не
        # лишняя речь: запас по словам и длительности растёт на каждый, но
        # нажимается фраза один раз. Второй Enter не безвреден: на вопросе
        # [y/N] он принял бы ответ по умолчанию.
        n = 1
        if strict:
            if self._t - self._fired.get(text, -self.REPEAT_S) < self.REPEAT_S:
                self._fired[text] = self._t
                log(f"повтор — уже нажато: {text!r}")
                return None
            hits = self._hits if self._run else self._last_hits
            hits[text] = n = hits.get(text, 0) + reps
        allowed = words * n + (0 if strict else self.MAX_EXTRA_WORDS)
        if said > allowed:
            # Пик — гипотеза, по которой насчитаны слова; vosk её потом
            # пересматривает («про белого» может схлопнуться в «пробел»), и по
            # одному пику отказ легко счесть чужой речью зря. Поэтому рядом —
            # последняя версия того же потока.
            peak, now = (self._run_text, self._heard_now()) if self._run \
                else (self._last_text, self._last_now)
            heard = shell.shown(peak) + ("" if now == peak else f", затем {shell.shown(now)}")
            log(f"отклонено (рядом сказано {said} слов при допустимых {allowed}): {text!r}"
                f" арбитр: {heard}")
            return None
        spoke = self._run_s or self._last_run_s
        limit = (self.RUN_S_ONE if words == 1 else self.RUN_S + self.RUN_S_MORE * (words - 1)) * n if strict else self.SPEECH_S + self.SPEECH_S_MORE * (words - 1)
        if spoke > limit:
            log(f"отклонено (речь длилась {spoke:.1f} с при допустимых {limit:.1f}): {text!r}")
            return None
        if strict:
            self._fired[text] = self._t
        return self._accept(text, res)

    def _accept(self, text, res):
        """Принятая фраза: запомнить, когда она началась (said_at)."""
        ws, n = res.get("result") or [], len(text.split())
        # «unk хватит консоль» — фраза начинается не с первого слова
        self.said_at = ws[-n]["start"] if len(ws) >= n else self._t
        return text, self._confidence(res)

    @staticmethod
    def _confidence(res):
        ws = res.get("result") or []
        return sum(w.get("conf", 1.0) for w in ws) / len(ws) if ws else 0.0


# ──────────────────────────── вставка текста ────────────────────────────────
class Paster:
    """Текст идёт через буфер обмена, а не через эмуляцию клавиш.

    uinput шлёт скан-коды, которые композитор переводит в буквы по текущей
    раскладке — кириллицу так набрать нельзя. Буфер работает с Unicode
    напрямую. xclip держит X11-селекцию, Mutter транслирует её в Wayland;
    фокус при этом не нужен, поэтому ничего не перехватывается.
    """

    # Через столько после вставки возвращаем буфер пользователя. Приложение
    # забирает данные уже после Ctrl+V, асинхронно: верни буфер сразу — и
    # вставилось бы прежнее содержимое.
    RESTORE_S = 1.0

    def __init__(self, inp):
        self.inp = inp
        self.lock = threading.Lock()
        self.timer = None
        self.saved = None          # (тип, байты) — что было в буфере до нас
        self.ours = None           # что положили мы сами

    @staticmethod
    def _read(target):
        try:
            return subprocess.run(
                ["xclip", "-o", "-selection", "clipboard", "-t", target],
                capture_output=True, timeout=1).stdout
        except subprocess.TimeoutExpired:
            return b""

    def _save(self):
        """Текст или картинка из буфера; None — если там пусто или что-то своё.

        Без этого «копировать», диктовка, «вставить» вставляли диктовку, а
        надиктованное так и лежало в буфере, открытое всем программам."""
        targets = self._read("TARGETS").decode(errors="replace").split()
        for t in ("UTF8_STRING", "text/plain;charset=utf-8", "image/png"):
            if t in targets:
                return t, self._read(t)
        return None

    @staticmethod
    def _put(data, target="UTF8_STRING"):
        # xclip сам уходит в фон держать селекцию, родитель выходит сразу.
        subprocess.run(["xclip", "-selection", "clipboard", "-t", target],
                       input=data, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=2)

    def _restore(self):
        with self.lock:
            # Пока ждали блокировку, прошла новая вставка со своим таймером.
            if self.timer is not threading.current_thread():
                return
            self.timer = None
            # Пользователь успел скопировать своё — его и оставляем.
            if self._read("UTF8_STRING") != self.ours:
                return
            target, data = self.saved or ("UTF8_STRING", b"")
            self._put(data, target)
            self.saved = self.ours = None

    def paste(self, text, keys=(E.KEY_LEFTCTRL, E.KEY_V)):
        # Только пустое: «пробел» в КОНСОЛИ — вставка из одного пробела, и
        # прежнее text.strip() молча её выбрасывало
        if not text:
            return
        with self.lock:
            # Вставки подряд: в буфере ещё наша прошлая, а вернуть надо то,
            # что было до первой.
            if self.timer:
                self.timer.cancel()
            else:
                self.saved = self._save()
            self.ours = text.encode("utf-8")
            self._put(self.ours)
            time.sleep(0.18)           # даём композитору принять владение
            self.inp.tap(*keys)
            self.timer = threading.Timer(self.RESTORE_S, self._restore)
            self.timer.daemon = True
            self.timer.start()
        log(f"вставлено: {shell.shown(text[:60])}")


# ──────────────────────────────── демон ─────────────────────────────────────
def vram_report():
    """Кто сколько держит видеопамяти — строкой для лога; None без nvidia-smi.

    whisper сам память не наращивает: 996 МБ после загрузки, 1072 МБ после
    первой фразы и дальше столько же на фразах 1–8 с (замер 27.09). Значит,
    нехватку создаёт сосед — браузер или ollama, — и видно это только в
    момент сбоя: через минуту память уже освободилась."""
    def query(*args):
        return subprocess.run(["nvidia-smi", *args, "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=5).stdout
    try:
        used, total = query("--query-gpu=memory.used,memory.total").split(",")
        apps = []
        for line in query("--query-compute-apps=pid,process_name,used_memory").splitlines():
            pid, name, mb = (x.strip() for x in line.split(","))
            apps.append(f"{os.path.basename(name.split()[0])}[{pid}] {mb}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return f"занято {used.strip()} из {total.strip()} МБ: " + ", ".join(apps)


def whisper_settings(log=print):
    """(модель, устройство, compute_type) из [whisper] в config.toml.

    Без видеокарты medium на CPU думает 4.7 с над фразой в 4 с (12 потоков),
    small — 1.6 с при том же тексте, base — 0.7 с, но путает слова чаще.
    Поэтому по умолчанию модель зависит от устройства."""
    cfg = shell.load_config(log).get("whisper", {})
    device = cfg.get("device", "auto")
    if device == "auto":
        import ctranslate2
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    model = cfg.get("model", "medium" if device == "cuda" else "small")
    return model, device, "int8_float16" if device == "cuda" else "int8"


def setting(cfg, section, key, default, convert):
    """Значение из config.toml или default, если строки нет или она негодна.

    Негодное — в журнал, а не исключение: опечатка в настройке не должна
    оставлять без голосового управления."""
    if key not in cfg.get(section, {}):
        return default
    try:
        return convert(cfg[section][key])
    except (TypeError, ValueError) as ex:
        log(f"[{section}] {key} не понят ({ex}), беру {default}")
        return default


def pair(v):
    """[ширина, высота] из config.toml — два положительных целых."""
    w, h = (int(x) for x in v)
    if w <= 0 or h <= 0:
        raise ValueError("нужны два положительных числа")
    return w, h


def speech_gate(noise):
    """Порог речи от медианы шума комнаты; им же считает measure_voice.py.

    Нижняя граница 80, а не 200: на этом микрофоне 200 приходилось выше
    девятого дециля живого голоса (p90=199), и арбитр видел лишь каждый
    десятый блок речи. Шум комнаты меряется тут же, пол нужен только от
    абсурдно тихой записи."""
    return float(min(max(noise * 2.0, 80.0), 1200.0))


class MicLost(Exception):
    """Звук перестал приходить — поток поднимет только перезапуск службы."""


class Worker:
    """Один поток, задачи по очереди, результат — concurrent.futures.Future.

    Не ThreadPoolExecutor: его потоки не демонические, и на выходе Python
    дожидается идущего запроса к LLM (таймаут 10 с). shutdown(wait=False,
    cancel_futures=True) не помогает — модуль сам ждёт свои потоки при
    выходе: задача на 3 с задержала выход на 3,05 с и с ним, и без него.
    """
    def __init__(self, name):
        self._jobs = queue.Queue()
        threading.Thread(target=self._loop, name=name, daemon=True).start()

    def submit(self, fn, *args):
        future = concurrent.futures.Future()
        self._jobs.put((future, fn, args))
        return future

    def _loop(self):
        while True:
            future, fn, args = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue            # отменена, пока ждала очереди
            try:
                future.set_result(fn(*args))
            except BaseException as ex:
                future.set_exception(ex)


class Daemon:
    SILENCE_S = 0.9        # столько тишины завершает фразу диктовки
    # Громкого звука меньше двух блоков — щелчок или кашель, а не слово. Длину
    # буфера для этого брать нельзя: в нём всегда SILENCE_S тишины после фразы
    # (p5 фрагментов 1.5 с при 45 короче 0.8 из 22399). «топ» — два блока
    # выше порога (0.25 с), щелчок — один (0.125 с; замер на синтезе).
    MIN_LOUD_S = 0.2
    # Длина буфера после обрезки хвоста: работает только на обрывках команды
    # выхода, всё прочее длиннее SILENCE_S.
    MIN_SHELL_S = 0.5      # «топ» и «сиди» короче порога диктовки в 0.8 с
    MAX_BUF_S = 8.0        # страховка: сброс, даже если порог не сработал
    # Через столько после выхода из КОНСОЛИ выгружаем LLM. keep_alive держал
    # бы её 30 минут, а рядом с ней (3390 МБ) у whisper остаётся ~1 ГБ —
    # запасные температуры диктовки в него не влезают (пик 1872 МБ). Минута —
    # чтобы короткий выход и возврат не стоили новой загрузки.
    LLM_IDLE_S = 60.0
    # Сколько ещё ждать vosk, если в его частичном результате уже команда
    # КОНСОЛИ: он засчитывает фразу позже, чем whisper забирает буфер
    # («вниз» — 2.12 с против 2.00, «эф три» — 2.62 против 2.25), и whisper
    # успевал вставить саму команду текстом: «нис», «ф», «вот».
    SHELL_HOLD_S = 1.0
    # Столько КОНСОЛЬ ждёт клавишной команды, потом уходит в СОН: забытая
    # КОНСОЛЬ выполнила бы «… ввод» из видео или созвона. Считаем по
    # клавишам, а не по тишине: колонки как раз не молчат, и whisper
    # вставляет услышанное из них — вставки поэтому тоже не в счёт. В СОН, а
    # не в КОМАНДЫ: там «ввод» — тот же Enter.
    SHELL_IDLE_S = 300.0
    # Столько СЕТКА ждёт номера, потом закрывается: человек отошёл, а сетка
    # висела бы над экраном. 30 с — на глаз.
    GRID_IDLE_S = 30.0
    # Столько «клик» в СЕТКЕ ждёт, пока окно сетки снимется. Само снятие —
    # доли секунды; дольше — окно зависло, и щелчок достался бы ему.
    GRID_HIDE_S = 1.0
    # Звук идёт блоками по 125 мс; если их нет столько секунд, поток мёртв:
    # гарнитуру выдернули или перезапустился PipeWire. Без этого q.get() ждал
    # вечно — демон глухой, трей не отвечает (меню разбирается в step()), а
    # служба числится «активной».
    MIC_TIMEOUT_S = 5.0

    def __init__(self):
        self.q = queue.Queue()
        self.state = SLEEP
        self.inp = Input()
        cfg = shell.load_config(log)
        self.mouse = Mouse(self.inp, read_span=screen_size)
        self.mouse.CHAR_PX = setting(cfg, "mouse", "char_px", Mouse.CHAR_PX, pair)
        self.mouse.start()
        self.head = Head(self.inp, cfg.get("head", {}).get("camera", 0))
        self.head.EXPOSURE = setting(cfg, "head", "exposure", Head.EXPOSURE, int)
        self.head.start()
        self.paster = Paster(self.inp)
        self.overlay = grid.Overlay(log)
        self.grid_first = None      # первый номер СЕТКИ, пока не сказан второй
        self._grid_used = 0.0
        self.llm = shell.load_llm(log)
        self.buf, self.silence, self.speech = [], 0.0, 0.0
        self.pastes = []           # вставки в строку терминала, ещё не выполненные
        self.line = ""             # чем кончается строка терминала: "", "text", " "
        self.whisper = None
        self.lang = "ru"
        self._wlock = threading.Lock()
        self._llm_timer = None
        self._shell_used = 0.0      # последняя клавишная команда КОНСОЛИ
        self._last_cmd, self._last_t = None, 0.0
        self._last_fail, self._last_fail_t = None, 0.0
        # Фраза КОНСОЛИ разбирается в своём потоке: пока LLM думала (~0,3 с,
        # при сбое — до таймаута 10 с), основной цикл стоял, и «хватит
        # консоль» и меню трея срабатывали с той же задержкой. Работник один —
        # фразы встают в строку в том порядке, в каком сказаны.
        self._shell_jobs = Worker("shell")
        # (future, текст, lead, конец речи на часах Ears) по порядку
        self._shell_pending = collections.deque()
        # Выбор в меню трея приходит из потока GTK, а режим меняет только
        # основной цикл: иначе смена режима легла бы посреди разбора фразы.
        self.requests = queue.Queue()
        global TRAY
        tray = Tray(lambda state, lang: self.requests.put((state, lang)), log)
        TRAY = tray if tray.ok else None
        self._load_vosk()

    # --- vosk с ограниченной грамматикой -----------------------------------
    def _load_vosk(self):
        import vosk
        vosk.SetLogLevel(-1)
        path = next((os.path.join(MODELS, d) for d in sorted(os.listdir(MODELS))
                     if d.startswith("vosk-model")), None)
        if not path:
            sys.exit("не найдена модель vosk в " + MODELS)
        log("загружаю vosk:", os.path.basename(path))
        self.vmodel = vosk.Model(path)
        self.vosk = vosk
        self._check_vocab()
        self.ears = Ears(self.vmodel, SLEEP)


    def _check_vocab(self):
        """Слова вне словаря vosk молча игнорируются — ловим это на старте.

        Такая команда не выдаёт ошибку, она просто никогда не срабатывает,
        поэтому без явной проверки причину почти невозможно нащупать.
        """
        words, missing = vocab_check(self.vmodel)
        if missing:
            log("ВНИМАНИЕ: нет в словаре vosk, команды с этими словами не сработают:",
                ", ".join(missing))
        else:
            log(f"словарь: все {len(words)} слов команд распознаваемы")

        # README называет число фраз в разделе «Как это устроено», и оно уже
        # разъезжалось с кодом (48 против 57): документация не исполняется,
        # заметить расхождение нечем. Сверяем при старте, рядом со словарём.
        try:
            with open(os.path.join(CODE, "README.md"), encoding="utf-8") as f:
                m = re.search(r"грамматика из (\d+) фраз", f.read())
        except OSError:
            m = None
        if m is None:
            log("README не прочитан, число команд не сверено")
        elif int(m.group(1)) != len(COMMANDS):
            log(f"ВНИМАНИЕ: README обещает {m.group(1)} фраз, в COMMANDS их {len(COMMANDS)}")

    # --- модель диктовки загружается лениво, чтобы старт был мгновенным -----
    def preload_whisper(self):
        """Прогрев в фоне: модель грузится 3.5 с, ждать этого при первой
        команде «печатать» было бы неприятно."""
        def preload():
            try:
                self._ensure_whisper()
            except Exception as ex:
                # «печатать» попробует ещё раз и сообщит о сбое сам
                log(f"whisper не загрузился ({type(ex).__name__}: {ex})")
        threading.Thread(target=preload, daemon=True).start()

    def _ensure_whisper(self):
        with self._wlock:
            if self.whisper is None:
                from faster_whisper import WhisperModel
                model, device, compute = whisper_settings(log)
                # LLM — только после whisper: ей подбирается число слоёв по
                # оставшейся памяти, а наоборот whisper не влезает вовсе.
                if self.llm is not None and device == "cuda":
                    try:
                        if self.llm.unload():
                            log("консоль: LLM выгружена — сначала грузится whisper")
                    except Exception as ex:
                        log(f"консоль: LLM не выгрузилась ({ex})")
                log(f"загружаю whisper {model} на {device} ({compute})...")
                self.whisper = WhisperModel(
                    model, device=device, compute_type=compute,
                    download_root=os.path.join(MODELS, "whisper"))
                log("whisper готов")

    def warm_llm(self):
        """Модель LLM грузится ~27 с — начинаем, пока пользователь говорит."""
        if self.llm is None:
            return
        def warm():
            try:
                self._ensure_whisper()      # ждём, если ещё грузится
                t0 = time.time()
                layers = self.llm.warm()
                less = (f", на видеокарте {layers} слоёв из {self.llm.max_gpu} — "
                        "больше не влезло" if layers is not None and layers < self.llm.max_gpu else "")
                log(f"консоль: LLM готова за {time.time()-t0:.1f}с{less}")
            except Exception as ex:
                log(f"консоль: LLM не прогрелась ({ex})")
        threading.Thread(target=warm, daemon=True).start()

    def _unload_idle_llm(self):
        if self.state == SHELL:       # вернулись, пока таймер срабатывал
            return
        try:
            if self.llm.unload():
                log("консоль: LLM выгружена — КОНСОЛЬ не нужна уже минуту")
        except Exception as ex:
            log(f"консоль: LLM не выгрузилась ({ex})")

    def set_state(self, new, note=""):
        if new == self.state:
            return
        if self._llm_timer is not None:
            self._llm_timer.cancel()
            self._llm_timer = None
        if self.state == SHELL and self.llm is not None:
            self._llm_timer = threading.Timer(self.LLM_IDLE_S, self._unload_idle_llm)
            self._llm_timer.daemon = True
            self._llm_timer.start()
        # Сказанное перед «хватит консоль» ещё встанет в строку, а СОН —
        # это «ничего не делать»: туда уходят и от забытой КОНСОЛИ.
        if new == SLEEP:
            self._drop_shell_jobs("режим СОН")
        if self.state == GRID:
            self.overlay.hide()
        self.state = new
        if new == SHELL:
            self._shell_used = time.time()
        if new == GRID:
            self._grid_used = time.time()
        self.buf, self.silence, self.speech = [], 0.0, 0.0
        self.pastes, self.line = [], ""
        self.mouse.stop()
        # Отдельное уведомление о голове сменило бы значок дважды подряд
        # (лицо → микрофон → режим), а расширение appindicators теряет
        # NewIcon, пришедший, пока оно ещё читает значок по первому
        # (_onSignalAsync в appIndicator.js) — значок застревает на прежнем
        self.head.off(notify=False)
        # В диктовке и во сне команды мыши недоступны, сказать «отпустить»
        # не получится — снимаем зажатие сами, пока это ещё возможно.
        self.inp.release_all()
        self.ears.listen(new)
        log(">>> режим:", new, note)
        feedback(new, note, self.lang)

    # --- выполнение команды -------------------------------------------------
    def run_command(self, action):
        kind, arg = action
        if kind == "key":
            self.inp.tap(*arg)
        elif kind == "times":
            action, n = arg
            for i in range(n):
                if i:
                    time.sleep(REPEAT_PAUSE_S)
                self.run_command(action)
        elif kind == "move":
            self.mouse.go(*arg)
        elif kind == "nudge":
            self.mouse.nudge(*arg)
        elif kind == "stop":
            # Часть пути возвращаем: фразу засчитывают через ~1,6 с после слова,
            # и курсор едет всё это время (см. Mouse.trail, Mouse.WORD_SHARE)
            self.mouse.stop(back_s=self.ears.since_word_s(self.mouse.WORD_SHARE))
            self.head.off()
        elif kind == "head":
            self.head.on() if arg else self.head.off()
        elif kind == "recenter":
            self.head.recenter()
        elif kind == "faster":
            (self.head if self.head.active else self.mouse).faster()
        elif kind == "slower":
            (self.head if self.head.active else self.mouse).slower()
        elif kind == "click":
            self.mouse.stop()
            if arg == "double":
                self.inp.click(E.BTN_LEFT, double=True)
            else:
                self.inp.click({"left": E.BTN_LEFT, "right": E.BTN_RIGHT,
                                "middle": E.BTN_MIDDLE}[arg])
        elif kind == "hold":
            self.inp.hold(E.BTN_LEFT, arg)
        elif kind == "wheel":
            self.inp.wheel(arg)
        elif kind == "mode":
            self.set_state(arg)
        elif kind == "dictate":
            # язык ставим до set_state: иначе первый фрагмент уйдёт в прежний
            self.lang = arg
            self._ensure_whisper()
            self.set_state(DICTATION, LANG_NAME[arg])
        elif kind == "shell":
            self._ensure_whisper()
            self.warm_llm()
            self.set_state(SHELL)
        elif kind == "grid":
            if not self.overlay.show():
                log("сетка недоступна: окно не поднялось")
                return
            self.grid_first = None
            self.set_state(GRID)

    def apply_request(self, state, lang):
        """Режим, выбранный в меню трея, или («ГОЛОВА», вкл/выкл)."""
        if state == "ГОЛОВА":
            if not lang:
                self.head.off()
                return
            # голова работает только в командах: из сна или диктовки сперва туда
            if self.state != COMMAND:
                self.apply_request(COMMAND, None)
            self.head.on()
            return
        if self.state == SHELL and state != SHELL:
            self.flush_dictation()          # сказанное до щелчка не пропадёт
        if self.state == DICTATION and (state, lang) != (DICTATION, self.lang):
            self.flush_dictation()          # сказанное до щелчка не пропадёт
            if state == DICTATION:          # смена языка без выхода из диктовки
                self.lang = lang
                log(">>> режим:", DICTATION, LANG_NAME[lang])
                feedback(DICTATION, LANG_NAME[lang], lang)
                return
        if state == DICTATION:
            self.run_command(("dictate", lang))
        elif state == SHELL:
            self.run_command(("shell", None))
        else:
            self.set_state(state)

    # --- диктовка -----------------------------------------------------------
    def flush_dictation(self, trim_s=0.0):
        """Распознать накопленный фрагмент; True — если что-то вставлено.

        Намеренно НЕ полагается на RMS-порог: он слишком зависит от микрофона
        и громкости голоса, а ошибается беззвучно. Речь от шума отличает
        Silero-VAD внутри whisper, порог нужен лишь для нарезки на фразы.
        """
        in_shell = self.state == SHELL
        heard, self.ears.heard = self.ears.heard, set()
        # где во фрагменте кончилась речь — см. _drop_exit_echo
        spoke_until = self.ears.now() - max(self.silence, trim_s)
        if not self.buf:
            self.silence = self.speech = 0.0
            return
        pcm = np.concatenate(self.buf)
        if trim_s > 0:                      # в хвосте команда выхода, не текст
            cut = int(trim_s * RATE)
            pcm = pcm[:-cut] if len(pcm) > cut else pcm[:0]
        secs = len(pcm) / RATE
        if len(pcm) == 0:          # вся запись ушла в обрезку хвоста — считать нечего
            self.buf, self.silence, self.speech = [], 0.0, 0.0
            return
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        peak = float(np.abs(pcm).max())
        loud = self.speech
        self.buf, self.silence, self.speech = [], 0.0, 0.0
        log(f"фрагмент {secs:.1f}с громко {loud:.2f}с rms={rms:.0f} пик={peak:.0f} порог={self.noise_gate:.0f}")
        if secs < (self.MIN_SHELL_S if in_shell else 0.8) or loud < self.MIN_LOUD_S:
            return
        audio = pcm.astype(np.float32) / 32768.0
        t0 = time.time()
        # В КОНСОЛИ — без запасных температур. Рядом с LLM свободно ~1 ГБ, а
        # запасная температура — пять гипотез разом: пик 1872 МБ против 1200
        # (замер 28.09), и все 66 сбоев out of memory в журнале — отсюда.
        # Толку от них тут и нет: на шуме они дают «онJust get moving!» за 9 с.
        extra = {"temperature": 0.0} if in_shell else {}
        segs, _ = self.whisper.transcribe(
            audio, language="ru" if in_shell else self.lang, beam_size=5, vad_filter=True,
            initial_prompt=shell.WHISPER_PROMPT if in_shell else None,
            condition_on_previous_text=False,
            no_speech_threshold=0.5, log_prob_threshold=-1.0, **extra)
        # whisper склонен «додумывать» текст на тишине — отбрасываем такие сегменты
        keep = [s.text.strip() for s in segs if s.no_speech_prob < 0.6]
        text = " ".join(keep).strip()
        text = strip_tail(text, heard, in_shell)
        log(f"whisper {time.time()-t0:.2f}с -> {shell.shown(text)}")
        if not text:
            return
        if in_shell and shell.prompt_echo(text):
            log("консоль: whisper повторил свою подсказку — не вставляю")
            return
        # «ввод», который vosk не засчитал: командой «вот» не бывает
        if in_shell and whisper_says_run(text):
            log(f"консоль: {shell.shown(text)} — это «ввод», не вставляю")
            return
        if in_shell:
            job = self._shell_jobs.submit(shell.to_command, text, shell.terminal_cwd(),
                                          self.llm, log)
            self._shell_pending.append((job, text, shell.leading_space(text), spoke_until))
        else:
            self.paster.paste(text)
        return True

    def _shell_results(self, wait=False):
        """Вставить разобранные фразы КОНСОЛИ по порядку; wait — дождаться
        всех: клавиша после фразы должна лечь после неё, как и сказана."""
        while self._shell_pending and (wait or self._shell_pending[0][0].done()):
            job, text, lead, _ = self._shell_pending.popleft()
            cmd = job.result()          # сбой разбора — в Daemon.fail()
            log(f"консоль: {shell.shown(text)} -> {shell.shown(cmd)}")
            # LLM ответила «НЕТ»: вставлять нечего
            if cmd or lead:
                self._paste_shell(cmd, lead)

    def _drop_shell_jobs(self, why):
        """Не вставлять фразы, которые ещё разбираются. Идущий запрос к LLM
        не прервать — его ответ просто никуда не попадёт."""
        for job, text, *_ in self._shell_pending:
            job.cancel()
            log(f"консоль: {shell.shown(text)} не вставлено — {why}")
        self._shell_pending.clear()

    def _drop_exit_echo(self):
        """Не вставлять фрагменты, в которых звучит сама фраза выхода.

        Фрагмент закрывается 0.9 с тишины, а vosk отдаёт фразу в тот же миг
        или блоком позже — и партиал в этот промежуток уже пуст, так что
        _command_in_flight буфер не держит (синтез: «хватит консоль» с пиком
        1200 — тишина 0.9 и фраза в одном блоке, партиал ''). whisper слышит
        в таком фрагменте «хуай», «ху». Отличить по тексту нельзя, по времени
        можно: речь, которая заходит за начало фразы выхода, — она сама.
        Сравнивается конец речи, а не конец фрагмента: после «ls», паузы ровно
        0.9 с и тихого «х» конец фрагмента заходит за начало «хватит».
        """
        start = self.ears.said_at
        keep = collections.deque()
        for item in self._shell_pending:
            job, text, _, spoke_until = item
            if spoke_until > start:
                job.cancel()
                log(f"консоль: {shell.shown(text)} не вставлено — это выход "
                    f"(речь до {spoke_until:.2f} с, выход с {start:.2f} с)")
            else:
                keep.append(item)
        self._shell_pending = keep

    def _paste_shell(self, cmd, lead=False, glue=False):
        """Вставить в строку терминала. Вторая вставка без «ввод» встала бы
        вплотную к первой — «ls -a» и «-l» давали «ls -a-l». Пробел входит во
        вставку, чтобы «нет» стёр его вместе с ней. Судим по self.line, а не
        по self.pastes: их сбрасывает любая клавиша, а после «вверх» в строке
        команда из истории — «1» вплотную к «./status.sh 192» давало 1921.
        glue — буква по буквам: с пробелом «ssh» вышел бы «s s h»."""
        text = (cmd if glue or not (lead or self.line == "text") or self.line == " "
                else " " + cmd)
        self.paster.paste(text, TERMINAL_PASTE)
        self.pastes.append((text, self.line))
        self.line = " " if text.endswith(" ") else "text"

    def drop_buffer(self):
        """Забыть недопечатанную речь, не распознавая её."""
        self.buf, self.silence, self.speech = [], 0.0, 0.0

    # --- аудио --------------------------------------------------------------
    def on_audio(self, indata, frames, t, status):
        if status:
            log("аудио:", status)
        self.q.put(bytes(indata))

    def handle_text(self, t, conf=0.0):
        """Реакция на распознанную фразу в текущем режиме."""
        if not t:
            return False
        # разрушающие команды — ещё и по уверенности: цена ошибки здесь выше
        need = need_conf(t, self.state)
        if conf < need:
            log(f"отклонено (уверенность {conf:.2f} < {need}): {t}")
            return False
        # одна фраза не должна выполниться дважды подряд
        now = time.time()
        if t == self._last_cmd and now - self._last_t < 0.6:
            return False
        def fire(action):
            self._last_cmd, self._last_t = t, now
            self.run_command(action)
            return True
        if self.state == SLEEP:
            if t in NORM_WAKE:
                log(f"команда: {t} ({conf:.2f})")
                return fire(NORM_WAKE[t])
        elif self.state == DICTATION:
            if t in NORM_EXIT:
                self.flush_dictation(trim_s=1.3)
                self.set_state(COMMAND); return True
        elif self.state == GRID:
            if t in NORM_GRID_CLOSE:
                self.set_state(COMMAND, f"сетка закрыта: {t}")
                return True
            if t in NORM_GRID:
                self._last_cmd, self._last_t = t, now
                self._grid_used = time.time()
                return self.grid_cell(NORM_GRID[t])
            if t in NORM_GRID_CLICK:
                self._last_cmd, self._last_t = t, now
                return self.grid_click(t, *NORM_GRID_CLICK[t])
        elif self.state == SHELL:
            if t in NORM_SHELL_EXIT:
                self._drop_exit_echo()
                self.flush_dictation(trim_s=1.3)
                self.set_state(COMMAND); return True
            if t == NORM_RUN:
                if conf < MIN_CONF:
                    log(f"отклонено (уверенность {conf:.2f} < {MIN_CONF}): {t}")
                    return False
                self._shell_used = time.time()
                # «Ввод» сказан раньше, чем допечаталась фраза, — её ещё никто
                # не видел. Выполнять вслепую нельзя: пусть сперва посмотрят.
                self.flush_dictation(trim_s=self.ears.tail_s())
                if self._shell_pending:
                    log("консоль: ввод отложен — команда ещё не в строке, скажите ещё раз")
                    return True
                self.inp.tap(E.KEY_ENTER)
                self.pastes, self.line = [], ""
                log(f"консоль: ввод ({conf:.2f})")
                return True
            if t in NORM_SHELL_KEYS:
                if t in STRICT and conf < MIN_CONF:
                    log(f"отклонено (уверенность {conf:.2f} < {MIN_CONF}): {t}")
                    return False
                self._shell_used = time.time()
                self.flush_dictation(trim_s=self.ears.tail_s())
                self._shell_results(wait=True)
                codes, n = NORM_SHELL_KEYS[t]
                # Через буфер, а не KEY_Y: uinput шлёт скан-код, и в русской
                # раскладке вышла бы «н». Вставка — чтобы «нет» её стёр.
                if isinstance(codes, str):
                    self._paste_shell(codes, glue=True)
                    self._last_cmd, self._last_t = t, time.time()
                    log(f"консоль: {t} ({conf:.2f})")
                    return True
                for i in range(n):
                    if i:
                        time.sleep(REPEAT_PAUSE_S)
                    self.inp.tap(*codes)
                # курсор ушёл или строка сменилась: «нет» стёр бы не то
                self.pastes = []
                # История кладёт в строку команду, Ctrl+C строку очищает (и
                # Ctrl+Z, Ctrl+U, Ctrl+R — см. CTRL_EMPTIES_LINE).
                # Остальное (курсор, стирание) пустую строку оставляет пустой,
                # а про непустую не скажешь, чем она теперь кончается.
                if codes in ([E.KEY_UP], [E.KEY_DOWN]):
                    self.line = "text"
                # После Tab следующее — вплотную: каталог дописан до «/»,
                # файл — с пробелом от самого шелла, а при нескольких
                # вариантах договаривают начало имени. С пробелом выходило бы
                # «cd Projects/ nohands».
                elif codes == [E.KEY_TAB]:
                    self.line = " "
                elif codes in CTRL_EMPTIES_LINE:
                    self.line = ""
                elif self.line:
                    self.line = "text"
                self._last_cmd, self._last_t = t, time.time()
                log(f"консоль: {t} ({conf:.2f})")
                return True
            if t == NORM_UNDO:
                if conf < MIN_CONF:
                    log(f"отклонено (уверенность {conf:.2f} < {MIN_CONF}): {t}")
                    return False
                self._shell_used = time.time()
                # недоговорённое перед «нет» тоже не нужно — его и отменяли
                self.drop_buffer()
                self._last_cmd, self._last_t = t, time.time()
                if self._shell_pending:     # ещё не в строке — стирать нечего
                    self._drop_shell_jobs(f"нет ({conf:.2f})")
                    return True
                if not self.pastes:
                    log(f"консоль: нет ({conf:.2f}) — стирать нечего")
                    return True
                last, self.line = self.pastes.pop()
                # Backspace, а не Ctrl+U: тот сотрёт и написанное до вставки
                for _ in last:
                    self.inp.tap(E.KEY_BACKSPACE)
                log(f"консоль: нет ({conf:.2f}) — стёрто {shell.shown(last)}")
                return True
        else:
            if t in NORM_COMMANDS:
                log(f"команда: {t} ({conf:.2f})")
                return fire(NORM_COMMANDS[t])
            log("не команда:", repr(t))
        return False

    def run(self):
        dev = sd.query_devices(kind="input")
        log("микрофон:", dev["name"])
        log("режим: СОН — скажите «проснись»")
        feedback(SLEEP, "скажите «проснись»")
        with sd.RawInputStream(samplerate=RATE, blocksize=BLOCK, dtype="int16",
                               channels=1, callback=self.on_audio):
            while True:
                try:
                    data = self.q.get(timeout=self.MIC_TIMEOUT_S)
                except queue.Empty:
                    raise MicLost(f"нет звука {self.MIC_TIMEOUT_S:.0f} с") from None
                try:
                    self.step(data)
                except Exception as ex:
                    self.fail(ex)

    def fail(self, ex):
        """Сбой обработки: сообщить и жить дальше в том же режиме.

        Раньше любое исключение роняло демона: служба поднимала его через 3 с
        во СНЕ, недопечатанное пропадало, а человек без рук видел лишь, что
        программа перестала слушать. Режим не меняем: сбой в «печатать»
        случается до смены режима, а сбой распознавания теряет одну фразу.
        Одна и та же ошибка подряд — один раз: если она на каждом блоке, это
        8 раз в секунду.
        """
        msg = f"{type(ex).__name__}: {ex}"
        now = time.time()
        repeat = msg == self._last_fail and now - self._last_fail_t < 10.0
        self._last_fail, self._last_fail_t = msg, now
        if repeat:
            return
        log("СБОЙ:", "".join(traceback.format_exception(ex)).rstrip())
        if "out of memory" in msg:
            log("видеопамять:", vram_report() or "nvidia-smi не ответил")
        play("dialog-error")
        if TRAY is not None:
            TRAY.mode(self.state, self.lang, f"сбой: {msg[:80]}")

    def step(self, data):
        """Один блок звука: команды, запросы из трея, накопление диктовки."""
        dur = len(data) / 2 / RATE
        pcm = np.frombuffer(data, dtype=np.int16)
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))

        if rms > self.noise_gate:
            self.head.hush()

        # 0) разобранное в КОНСОЛИ — до команд: они сказаны позже
        self._shell_results()

        # 1) команды — по завершённой фразе, которую одобрил арбитр
        got = self.ears.feed(data)
        if got:
            self.handle_text(*got)
        # «Стоп» по частичному результату: финальный приходит через ~1,6 с
        # после слова (замер на синтезе: частичный — через 0,1 с), и курсор
        # всё это время едет. Барьеры тут не нужны — ложный «стоп» лишь
        # останавливает курсор, а путь вернёт настоящий (run_command).
        elif self.state == COMMAND and self.mouse.moving.is_set() \
                and self.ears.partial() == NORM_STOP:
            self.mouse.stop()
            log("мышь: замерла по частичному «стоп»")
        while not self.requests.empty():
            self.apply_request(*self.requests.get())
        self._shell_idle()
        self._grid_idle()

        # 2) накопление речи для диктовки
        if self.state in (DICTATION, SHELL):
            self.buf.append(pcm)
            if rms > self.noise_gate:
                self.speech += dur
                self.silence = 0.0
            else:
                self.silence += dur
            secs = sum(len(b) for b in self.buf) / RATE
            done = self.speech > 0 and self.silence >= self.SILENCE_S
            if done and self.state == SHELL and self._command_in_flight() \
                    and self.silence < self.SILENCE_S + self.SHELL_HOLD_S:
                done = False        # это команда: её судит vosk
            if done or secs >= self.MAX_BUF_S:
                self.flush_dictation()

    def _shell_idle(self):
        """КОНСОЛЬ давно без клавиш — в СОН (см. SHELL_IDLE_S)."""
        if self.state == SHELL and time.time() - self._shell_used >= self.SHELL_IDLE_S:
            # недоговорённое не вставляем: скорее всего, это те же колонки
            self.set_state(SLEEP, f"КОНСОЛЬ без дела {self.SHELL_IDLE_S / 60:.0f} мин")

    def _grid_idle(self):
        """СЕТКА давно без номера — закрыть (см. GRID_IDLE_S)."""
        if self.state == GRID and time.time() - self._grid_used >= self.GRID_IDLE_S:
            self.set_state(COMMAND, f"сетка без дела {self.GRID_IDLE_S:.0f} с")

    def grid_cell(self, n):
        """Номер в СЕТКЕ: первый — клетка крупно, второй — курсор в точку."""
        if self.grid_first is None:
            self.grid_first = n
            self.overlay.zoom(n)
            log(f"сетка: клетка {n:02d}")
            return True
        first, self.grid_first = self.grid_first, None
        return self._grid_put(lambda rect: grid.cell_point(rect, first, n),
                              f"клетка {first:02d} {n:02d}")

    def grid_click(self, t, btn, double):
        """«клик» после первого номера: курсор в центр крупной клетки и щелчок.
        До номера щёлкать некуда — сетка лишь закрывает экран."""
        if self.grid_first is None:
            log(f"сетка: «{t}» до номера — сперва номер клетки")
            return False
        first, self.grid_first = self.grid_first, None
        if not self._grid_put(lambda rect: grid.cell_center(rect, first),
                              f"{t} в клетку {first:02d}"):
            return True
        # Окно сетки ловит щелчки: щёлкать, только когда оно снято
        if not self.overlay.hidden.wait(self.GRID_HIDE_S):
            log(f"сетка: окно не закрылось за {self.GRID_HIDE_S} с — клика не было")
            return True
        self.inp.click(btn, double=double)
        return True

    def _grid_put(self, point, note):
        """Курсор в point(монитор), сетка закрыта, режим — КОМАНДЫ.
        False — размер экрана неизвестен, курсор не тронут."""
        # Номер может прийти раньше, чем окно назвало свой монитор, а Mutter
        # может не ответить: тогда одно восполняет другое.
        span, rect = screen_size(), self.overlay.rect
        if span is None and rect is not None:
            span = (rect[0] + rect[2], rect[1] + rect[3])
        if span is None:
            self.set_state(COMMAND, "сетка: размер экрана неизвестен")
            return False
        self.inp.put(*point(rect or (0, 0, *span)), span)
        self.set_state(COMMAND, note)
        return True

    def _command_in_flight(self):
        """vosk уже слышит команду КОНСОЛИ, но ещё не засчитал её?"""
        partial = self.ears.partial()
        return partial in NORM_SHELL_KEYS or partial in STRICT or partial in NORM_SHELL_EXIT

    def calibrate(self):
        """Порог шума считается от реального шума комнаты и микрофона.

        Медиана по блокам, а не среднее по всей записи: одиночный посторонний
        звук (стук, уведомление) задирает среднее в разы и тихо ломает всё,
        что на порог опирается. Медиана к такому выбросу устойчива.
        Плюс жёсткий потолок — порог физически не может стать абсурдным.
        """
        log("калибрую шум комнаты (2 с, помолчите)...")
        rec = sd.rec(int(2 * RATE), samplerate=RATE, channels=1, dtype="int16")
        sd.wait()
        a = rec.reshape(-1).astype(np.float32)
        blocks = a[:len(a) // BLOCK * BLOCK].reshape(-1, BLOCK)
        per_block = np.sqrt((blocks ** 2).mean(axis=1))
        noise = float(np.median(per_block))
        self.noise_gate = speech_gate(noise)
        self.ears.gate = self.noise_gate
        log(f"шум(медиана)={noise:.0f} разброс={per_block.min():.0f}..{per_block.max():.0f}"
            f" порог речи={self.noise_gate:.0f}")


# Журнал копится без ротации: служба открывает его на дозапись. При каждом
# запуске сверх LOG_MAX оставляем последние LOG_KEEP.
LOG_MAX, LOG_KEEP = 5 << 20, 1 << 20


def trim_logs():
    """Обрезать voice.log и voice.err, если служба пишет в файлы."""
    for fd in (1, 2):
        try:
            if os.fstat(fd).st_size <= LOG_MAX:
                continue
            path = os.readlink(f"/proc/self/fd/{fd}")
            with open(path, "rb") as f:          # сам fd открыт только на запись
                f.seek(-LOG_KEEP, os.SEEK_END)
                tail = f.read()
            tail = tail[tail.find(b"\n") + 1:]   # с начала строки
            # Дозапись идёт в конец, каким бы он ни стал: после усечения — с нуля
            os.ftruncate(fd, 0)
            os.write(fd, tail)
        except OSError:
            continue        # терминал, а не файл, — обрезать нечего


def phrase_list():
    """Шпаргалка для `nohands cmds` — из тех же таблиц, что слушает демон.

    Строка в deploy/nohands отставала от кода: в ней остался убранный
    «русский» и не было ни «писать», ни клавиш КОНСОЛИ.
    """
    def row(title, phrases):
        return f"{title}:\n  " + " | ".join(phrases)

    def times(phrases):
        return f"повтор «… N раз», N от двух до десяти: {', '.join(phrases)}"

    keys = [k for k in SHELL_KEYS if k not in CTRL_KEYS]
    return "\n\n".join([
        row("СОН → КОМАНДЫ", list(WAKE)),
        row("ДИКТОВКА → КОМАНДЫ", EXIT_DICTATION),
        row("КОНСОЛЬ → КОМАНДЫ", SHELL_EXIT),
        row("КОМАНДЫ", sorted(COMMANDS)) + "\n  " + times(COMMAND_REPEATABLE),
        row("КОНСОЛЬ", [SHELL_RUN, SHELL_UNDO] + keys) + "\n  " + times(REPEATABLE),
        row("КОНСОЛЬ, буквы", list(SHELL_LETTERS)),
        row("КОНСОЛЬ, Ctrl+буква", list(CTRL_KEYS)),
        row("СЕТКА (после «мышь сетка»)", ["00–99: «семь», «ноль семь», «сорок семь», "
                                          "«четыре семь»",
                                          "клик / правый клик / двойной клик (после первого номера)",
                                          "нет", "стоп"]),
    ])


def main():
    # Журнал и данные — только владельцу: в журнал попадает то, что человек
    # говорит, а домашний каталог бывает открыт на чтение всем (755).
    os.umask(0o077)
    logs = os.path.join(HOME, "logs")
    os.makedirs(logs, exist_ok=True)
    for path in [HOME, logs] + [os.path.join(logs, n) for n in os.listdir(logs)]:
        try:
            os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        except OSError as ex:
            log("права не поставлены:", path, ex)
    trim_logs()
    shell.LOG_TEXT = bool(shell.load_config(log).get("log", {}).get("text", False))
    log("журнал: сказанное", "пишется текстом" if shell.LOG_TEXT else "не пишется, только длина")
    # systemctl stop и restart шлют SIGTERM, а по умолчанию Python на нём
    # умирает сразу, мимо finally: камера так и оставалась с усилением 255.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    d = Daemon()
    d.preload_whisper()      # греем модель диктовки параллельно с калибровкой
    d.calibrate()
    try:
        d.run()
    except (KeyboardInterrupt, SystemExit):
        log("остановлен")
    except MicLost as ex:
        # Код 1, а не SystemExit внутри run(): его ловит строка выше, выход
        # с кодом 0 — и Restart=on-failure службу не поднимает.
        log(f"микрофон: {ex} — выхожу, служба запустит заново")
        sys.exit(1)
    finally:
        d.head.stop()


if __name__ == "__main__":
    main()
