# Сетка — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** режим СЕТКА: «мышь сетка» / «мышь координаты» показывает поверх экрана сетку 10×10 с номерами 00–99, первый номер делит клетку на 10×10 мелких, второй ставит курсор в центр мелкой клетки и возвращает в КОМАНДЫ.

**Architecture:** новый файл `grid.py`: таблица номеров и геометрия (чистые функции), окно сетки — отдельный постоянный процесс на GTK4 (`python grid.py`), с ним демон говорит строками через stdin/stdout (класс `Overlay`). GTK4 в процесс демона не загружается: там уже GTK3 трея, вместе они не уживаются. Курсор ставит третье uinput-устройство с абсолютными координатами (`Input.put`). В `voiced.py` — режим `GRID` со своей грамматикой.

**Tech Stack:** Python 3 из `~/.local/share/nohands/venv`, PyGObject + GTK 4.22 (cairo в `Gtk.DrawingArea`), python-evdev `UInput` с `EV_ABS`, vosk.

**Spec:** `docs/superpowers/specs/2026-10-25-mouse-grid-design.md` — читать вместе с планом.

## Global Constraints

- Фразы — только эти: «мышь сетка», «мышь координаты» (в КОМАНДАХ); номера в обоих видах, «нет», «стоп» (в СЕТКЕ). Других фраз не придумывать.
- Сетка видна не позже ~0,3 с после засчитанной фразы.
- Сбой или смерть окна сетки не роняют демона (`Daemon.fail`, лог).
- Ничего не зашивать под 1920×1080 и под положение камеры: размеры — от геометрии монитора.
- Язык кода, комментариев, журнала, коммитов — русский; тема коммита «Сетка: …», тело — почему.
- Комментарии — зачем и с замером; что делает код, видно из кода.
- Тесты — скрипты в стиле `test_mouse.py` (`check()`, итог `ИТОГ: ПРОЙДЕН`); звук через колонки не играть, `feedback`/`play` в тестах подменять.
- Python: `PY=~/.local/share/nohands/venv/bin/python`.

## Review Focus

- Процесс окна не запустился (служба стартовала раньше сеанса, нет дисплея) или умер: «мышь сетка» пишет в журнал и остаётся в КОМАНДАХ, демон живёт — тест в Task 3.
- Второй номер сказан раньше, чем окно сообщило геометрию монитора: курсор ставится по рамке `screen_size()` — тест в Task 3.
- `screen_size()` вернул `None`: размер рамки берётся из геометрии монитора окна (`x + w`, `y + h`) — тест в Task 3.
- Режим сменили из трея, пока сетка открыта: окно прячется — тест в Task 3.
- Человек отошёл: через `GRID_IDLE_S` = 30 с сетка закрывается — тест в Task 3.

---

### Task 1: Номера и геометрия

**Files:**
- Create: `grid.py`
- Test: `test_grid.py`

**Interfaces:**
- Produces:
  - `grid.NUMBERS: dict[str, int]` — фраза (нормализованная: без «ё», нижний регистр) → номер 0..99.
  - `grid.cell_point(rect: tuple[float, float, float, float], first: int, second: int) -> tuple[float, float]` — центр мелкой клетки `second` в крупной `first`, `rect` = (x, y, ширина, высота) монитора в логических пикселях рамки.
  - `grid.ABS_MAX = 32767`; `grid.abs_value(v: float, extent: float) -> int`.

- [ ] **Step 1: Написать `test_grid.py` с падающими проверками**

Шапка как у `test_mouse.py` (`check`, `fails`, `main`, `ИТОГ`). Проверки:

```python
import grid as G
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
check("200 фраз", len(G.NUMBERS) == 200)
check("без ё", not any("ё" in p for p in G.NUMBERS))
# 1920×1080: клетка 47 — 192×108, мелкая 23 — 19,2×10,8
check("47/23", G.cell_point((0, 0, 1920, 1080), 47, 23) == (1411.2, 459.0))
check("00/00", G.cell_point((0, 0, 1920, 1080), 0, 0) == (9.6, 5.4))
check("99/99 у края", G.cell_point((0, 0, 1920, 1080), 99, 99) == (1910.4, 1074.6))
check("смещение монитора", G.cell_point((1920, 0, 1920, 1080), 0, 0) == (1929.6, 5.4))
# GNOME растягивает 0..32767 на [0, ширина): значение 24575 даёт 1439.99
check("abs 1440", G.abs_value(1440, 1920) == 24575)
check("abs 0", G.abs_value(0, 1920) == 0)
check("abs за краем", G.abs_value(2500, 1920) == G.ABS_MAX)
check("abs меньше нуля", G.abs_value(-5, 1920) == 0)
```

Сравнение дробей — через `round(..., 6)` или `math.isclose`, по вкусу.

- [ ] **Step 2: Запустить — падает**

Run: `$PY test_grid.py`
Expected: `ModuleNotFoundError: No module named 'grid'`

- [ ] **Step 3: Написать `grid.py`: `NUMBERS`, `cell_point`, `abs_value`**

Шапка модуля — как у `shell.py`: что это и что он ничего не знает о демоне. `NUMBERS` строится из слов цифр («ноль»…«девять»), «десять»…«девятнадцать», десятков «двадцать»…«девяносто»: одна цифра → 0–9; пара цифр → 00–99; «десять»…«девятнадцать»; десяток → 20…90; десяток + цифра 1–9 → 21…99. `abs_value` = `round(v * ABS_MAX / extent)`, зажатый в 0..ABS_MAX; в комментарии — замер опыта 25.10 (делитель `extent`, а не `extent - 1`: с ним курсор вставал на 0,7 пикс правее). Модуль верхнего уровня не импортирует GTK.

- [ ] **Step 4: Запустить — проходит**

Run: `$PY test_grid.py`
Expected: все `ok`, `ИТОГ: ПРОЙДЕН`

- [ ] **Step 5: Commit**

```bash
git add grid.py test_grid.py
git commit -m "Сетка: номера 00–99 и центр клетки"
```

---

### Task 2: Окно сетки, абсолютный курсор и проверка «поверх»

Это проверка из спецификации, которая блокирует всё остальное: если окно не встаёт поверх надёжно, **остановиться и вернуться к пользователю** (запасной путь — расширение GNOME Shell — он не одобрял).

**Files:**
- Modify: `grid.py` (окно и `Overlay`)
- Modify: `voiced.py` — класс `Input` (`voiced.py:42-102`)
- Test: `test_grid.py` (ключ `--live`)

**Interfaces:**
- Consumes: `grid.cell_point`, `grid.abs_value`, `grid.ABS_MAX` (Task 1).
- Produces:
  - Протокол процесса окна, по строке на сообщение. Демон → окно: `show`, `zoom NN`, `hide`. Окно → демон: `ready`, `shown X Y W H` (геометрия монитора окна), `enter X Y` (курсор вошёл в окно, координаты рамки), `pointer X Y` (движение), `hidden`.
  - `grid.Overlay(log)`: `.ok: bool`, `.show()`, `.zoom(n: int)`, `.hide()`, `.rect: tuple | None` (последняя `shown`), `.entered: threading.Event` (сброс на `show`), `.last_pointer: tuple | None`. Запускает `[sys.executable, grid.py]`; если процесс умер — `show()` поднимает его заново и пишет об этом в журнал; если не поднялся — `ok = False`, `show()` возвращает `False`.
  - `Input.put(x: float, y: float, span: tuple[float, float]) -> None` — курсор в точку рамки; `span` — размер рамки (`screen_size()`).
  - `Input.abs` — `UInput` «nohands-pointer»: `EV_KEY` `BTN_LEFT/RIGHT/MIDDLE`, `EV_ABS` `ABS_X`/`ABS_Y` 0..`ABS_MAX` (как в опыте; без кнопок udev не сочтёт его мышью).

- [ ] **Step 1: Проверка `Input.put` на подменённом устройстве — написать, падает**

В `test_grid.py` (без `--live`): объект `Input` без `__init__` (`V.Input.__new__(V.Input)`), `abs` — подделка, копящая `write`. `put(1440, 486, (1920, 1080))` пишет `ABS_X` 24575 и `ABS_Y` 14745 и `syn`. `put(…, span=None)` — не падает и ничего не пишет (размер обязан дать вызывающий, см. Task 3).

Run: `$PY test_grid.py` — Expected: FAIL (`put` нет).

- [ ] **Step 2: Добавить устройство и `Input.put` в `voiced.py`**

Комментарий в `Input`: почему третье устройство, а не относительное (замер 25.10: сдвиг на 5 пикс дал 1,7 — GNOME меняет скорость относительных движений). Run: `$PY test_grid.py` — Expected: `ИТОГ: ПРОЙДЕН`.

- [ ] **Step 3: Окно сетки в `grid.py` (`main()` под `if __name__ == "__main__"`)**

Как в опыте (спецификация, раздел «Замеры»): `Gtk.Application`, на каждый `show` — новое окно `decorated=False`, `fullscreen()`, `present()`, прозрачный фон CSS, рисование cairo; на `hide` — окно уничтожается. Команды читаются из stdin в потоке и передаются в главный цикл через `GLib.idle_add`. Геометрию монитора — `display.get_monitor_at_surface(surface).get_geometry()` после отображения → `shown`. `EventControllerMotion`: `enter` и `motion` → `enter X Y` / `pointer X Y` в координатах рамки (локальные + смещение монитора). Рисунок: линии клеток красные полупрозрачные (0.9, 0.1, 0.1, 0.45), номера `{n:02d}` жирным красным с белой обводкой; шрифт крупной сетки `22 * H / 1080`, мелкой `7 * H / 1080` (одобрено на наброске); на `zoom NN` клетка NN обведена рамкой шириной 2 и поделена на 10×10. stdout — `flush` после каждой строки. `LC_NUMERIC=C` — как в `voiced.py`.

- [ ] **Step 4: Класс `Overlay` в `grid.py`**

Читающий поток разбирает строки окна, обновляет `rect`, `entered`, `last_pointer`; прочее — в `log`. Окно процесса — в журнал демона через `log` только по `shown` и ошибкам, не на каждое `pointer`.

- [ ] **Step 5: `$PY test_grid.py --live` — проверка «поверх» и точности**

Режим `--live` (без звука): `Overlay` + настоящий `Input`; десять раз подряд: `show()`, ждать `entered` до 0,5 с, `zoom(47)`, `Input.put(*cell_point(rect, 47, 23), span)`, ждать `last_pointer`, `hide()`. Проверки: `entered` всякий раз (окно поверх: курсор над полноэкранным окном входит в него без движения — так было во втором запуске опыта, в первом входа не было); `last_pointer` в пределах 1 пикс от цели; от `show()` до `shown` — не дольше 0,3 с. Перед запуском дать пользователю окно в фокусе: терминал, затем браузер, затем полноэкранное окно — по `speech.say` сказать, какое окно открыть, и ждать 5 с.

Run: `$PY test_grid.py --live`
Expected: `ИТОГ: ПРОЙДЕН`. Если `entered` хоть раз не пришёл — попробовать поднять окно (`present()` повторно после `map`, токен активации из переменной окружения); не вышло — **стоп, к пользователю**.

- [ ] **Step 6: Commit**

```bash
git add grid.py voiced.py test_grid.py
git commit -m "Сетка: окно поверх экрана и курсор в точку"
```

---

### Task 3: Режим СЕТКА в демоне

**Files:**
- Modify: `voiced.py` — `SLEEP, COMMAND, …` (`:35`), `COMMANDS` (`:682`), `grammar_for` (`:1014`), `vocab_check` (`:1031`), `Daemon` (`__init__`, `set_state`, `run_command`, `handle_text`, `step`)
- Modify: `tray.py` — `ICONS`
- Test: `test_grid.py`, `test_commands.py`

**Interfaces:**
- Consumes: `grid.NUMBERS`, `grid.cell_point`, `grid.Overlay`, `Input.put` (Tasks 1–2).
- Produces: `GRID = "СЕТКА"`; `NORM_GRID: dict[str, int]` (= `grid.NUMBERS` через `norm`); `NORM_GRID_CLOSE = [norm("нет"), norm("стоп")]`; `Daemon.GRID_IDLE_S = 30.0`; `Daemon.overlay`, `Daemon.grid_first: int | None`, `Daemon._grid_used: float`; действие `("grid", None)` в `run_command`.

- [ ] **Step 1: Проверки режима — написать, падают**

В `test_grid.py`: демон без `__init__` (`V.Daemon.__new__`) с подделками `overlay` (копит вызовы, `rect`, `ok`), `inp` (копит `put`, `release_all`), `mouse`, `head`, `ears` (`listen` копит режимы); `V.feedback` и `V.screen_size` подменены. Проверки:
- `run_command(("grid", None))` в КОМАНДАХ → `state == GRID`, `overlay.show` вызван, `ears.listen(GRID)`;
- `overlay.ok = False` → состояние остаётся КОМАНДЫ, в журнале «сетка недоступна»;
- `handle_text("сорок семь", 1.0)` → `overlay.zoom(47)`, состояние GRID; затем `handle_text("два три", 1.0)` при `rect = (0, 0, 1920, 1080)` → `inp.put(1411.2, 459.0, span)`, состояние КОМАНДЫ, `overlay.hide` вызван;
- то же при `rect = None` → точка считается по `(0, 0, *screen_size())`;
- то же при `screen_size() is None` и `rect = (0, 0, 1920, 1080)` → `span = (1920, 1080)`;
- `handle_text("нет", 1.0)` и `handle_text("стоп", 1.0)` на любом шаге → КОМАНДЫ, `put` не вызван;
- `set_state(SLEEP)` из GRID → `overlay.hide`;
- `_grid_used` на 31 с в прошлом, `step` (или выделенный `_grid_idle()`) → КОМАНДЫ;
- `grammar_for(GRID)` — ровно `NORM_GRID` + `нет`, `стоп`, `[unk]`; в `grammar_for(COMMAND)` номеров нет, «мышь сетка» и «мышь координаты» есть.

Run: `$PY test_grid.py` — Expected: FAIL.

- [ ] **Step 2: Реализовать в `voiced.py` и `tray.py`**

`COMMANDS`: `"мышь сетка": ("grid", None)`, `"мышь координаты": ("grid", None)`. `run_command("grid")`: `overlay.show()` не удался → лог «сетка недоступна», режим не менять; иначе `grid_first = None`, `set_state(GRID)`. `set_state`: при выходе из GRID — `overlay.hide()`; при входе — `_grid_used = time.time()`. `handle_text`, ветка GRID — как в спецификации («Как работает»); после второго номера `set_state(COMMAND, f"клетка {first:02d} {second:02d}")`. Таймаут — `_grid_idle()` рядом с `_shell_idle()` в `step`, примечание к смене режима «сетка без дела 30 с». `vocab_check` — добавить `NORM_GRID` и фразы закрытия. `Daemon.__init__` — `self.overlay = grid.Overlay(log)`. `tray.ICONS["СЕТКА"]` — значок по выбору строителя из `/usr/share/icons` (символьный, есть в теме Adwaita). Звук режима — `feedback` уже даёт «bell» для неизвестного режима; оставить.

Run: `$PY test_grid.py` — Expected: `ИТОГ: ПРОЙДЕН`.

- [ ] **Step 3: `test_commands.py` — фразы на синтезе**

В `REAL` — «мышь сетка», «мышь координаты». Новый раздел «── СЕТКА ──»: `Ears(model, V.GRID)`, каждая из фраз «семь», «ноль семь», «сорок семь», «четыре семь», «десять», «девяносто девять», «нет», «стоп» засчитывается ровно ею (функция вроде `heard`, но с режимом GRID и проверкой `t in V.NORM_GRID or t in V.NORM_GRID_CLOSE`). Задержку однозначного номера печатать (замер, не проверка): по спецификации ожидается ~0,5 с сверх двузначного.

Run: `$PY test_commands.py` (~2 мин)
Expected: `ИТОГ: ПРОЙДЕН`; ложных срабатываний на `FOREIGN` не больше, чем до правки (сравнить с прогоном на `develop` до задачи).

- [ ] **Step 4: Commit**

```bash
git add voiced.py tray.py test_grid.py test_commands.py
git commit -m "Сетка: режим СЕТКА — «мышь сетка», номер клетки, курсор в точку"
```

---

### Task 4: Документация и живая проверка

**Files:**
- Modify: `README.md` (команды мыши; «Известные ограничения», `README.md:287-289`)
- Modify: `docs/TODO.md`
- Modify: `AGENTS.md` (таблица файлов: `grid.py`, `test_grid.py`)
- Modify: `voiced.py` — `phrase_list()` (`voiced.py:2175`), строка для СЕТКИ.

- [ ] **Step 1: README** — сетка рядом с командами мыши: фразы, два шага, «нет»/«стоп», 30 с. Из «Известных ограничений» убрать пункт про layer-shell.
- [ ] **Step 2: `docs/TODO.md`** — убрать «Сетка с номерами поверх экрана»; в пункт «Проверить живьём: скорость…» дописать: положение курсора видит полноэкранное окно (`enter`/`pointer` в `grid.py`) — путь к точному откату у края.
- [ ] **Step 3: `phrase_list()`** — строка «СЕТКА» (номера кратко: «00–99: «семь», «ноль семь», «сорок семь», «четыре семь»», «нет», «стоп»). Run: `$PY -c "import voiced; print(voiced.phrase_list())"` (так её зовёт `nohands cmds`) — в выводе есть строка СЕТКИ.
- [ ] **Step 4: Commit**

```bash
git add README.md docs/TODO.md AGENTS.md voiced.py
git commit -m "Сетка: README и доработки"
```

- [ ] **Step 5: Живая проверка — пользователь**

Спросить пользователя, можно ли перезапустить службу; после `nohands restart` по `speech.say` попросить: «мышь сетка», «два ноль», «ноль один» (значок панели), «клик»; потом «мышь координаты», «нет». Смотреть `nohands log`: строки режима СЕТКА, «клетка 20 01», отсутствие «курсор не вошёл в окно». Фокус после закрытия: пользователь говорит, ушёл ли «клик» в то окно, где был фокус.
