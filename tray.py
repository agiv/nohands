"""Значок в трее: показывает режим и переключает его из меню.

GNOME сам трей не рисует — значок показывает расширение
ubuntu-appindicators по протоколу StatusNotifierItem, а libayatana-appindicator
говорит на нём за нас. Если расширения нет или оно ещё не поднялось,
библиотека зарегистрирует значок, когда оно появится.

GTK живёт в своём потоке со своим главным циклом: основной поток демона
занят звуком. Все обращения к виджетам идут через GLib.idle_add, то есть
выполняются в потоке GTK, а выбор в меню уходит демону через колбэк —
демон сам решает, в каком потоке его применить.
"""

import locale, threading

# (режим, язык) -> подпись в меню; язык важен только для диктовки
ITEMS = [(("СОН", None), "Сон"),
         (("КОМАНДЫ", None), "Команды"),
         (("ДИКТОВКА", "ru"), "Диктовка: русский"),
         (("ДИКТОВКА", "en"), "Диктовка: английский"),
         (("КОНСОЛЬ", None), "Консоль")]
ICONS = {"СОН": "microphone-sensitivity-muted-symbolic",
         "КОМАНДЫ": "audio-input-microphone-symbolic",
         "ДИКТОВКА": "document-edit-symbolic",
         "КОНСОЛЬ": "utilities-terminal-symbolic",
         "СЕТКА": "view-grid-symbolic"}
# Голова включается только в командах и гаснет при любой смене режима, так что
# этот значок, заменяя значок режима, ничего не прячет
HEAD_ICON = "auth-face-symbolic"


class Tray:
    def __init__(self, on_select, log):
        self.on_select = on_select      # из потока GTK: (режим, язык) или («ГОЛОВА», вкл)
        self.log = log
        self.ready = threading.Event()
        self.ok = False
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        self.ready.wait(5)

    def _run(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AI
        except (ImportError, ValueError) as ex:
            self.log("трей: нет библиотек —", ex)
            self.ready.set()
            return
        # Импорт Gtk сам вызывает init и ставит локаль процесса из окружения,
        # а LC_NUMERIC=ru_RU заставляет vosk писать дробные числа в JSON через
        # запятую. Основное — LC_NUMERIC=C в окружении (voiced.py); здесь —
        # на случай LC_ALL, который перекрывает LC_NUMERIC.
        locale.setlocale(locale.LC_NUMERIC, "C")
        if not Gtk.init_check()[0]:
            self.log("трей: нет дисплея")
            self.ready.set()
            return
        self.GLib = GLib
        self.ind = AI.Indicator.new("nohands", ICONS["СОН"],
                                    AI.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AI.IndicatorStatus.ACTIVE)
        self.ind.set_title("nohands")

        menu = Gtk.Menu()
        self.status = Gtk.MenuItem(label="")
        self.status.set_sensitive(False)
        self.head = Gtk.MenuItem(label="")
        self.head.set_sensitive(False)
        menu.append(self.status)
        menu.append(self.head)
        menu.append(Gtk.SeparatorMenuItem())
        self.radio, group = {}, None
        for key, label in ITEMS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            item.connect("toggled", self._toggled, key)
            menu.append(item)
            self.radio[key] = item
        menu.append(Gtk.SeparatorMenuItem())
        self.head_toggle = Gtk.CheckMenuItem(label="Управление головой")
        self.head_toggle.connect("toggled", self._head_toggled)
        menu.append(self.head_toggle)
        menu.show_all()
        self.head.hide()                 # появится, когда включат голову
        self.ind.set_menu(menu)
        self._syncing = False
        self.state, self.head_on = "СОН", False
        self.ok = True
        self.ready.set()
        Gtk.main()

    def _toggled(self, item, key):
        # toggled приходит и при снятии отметки, и при нашей же синхронизации
        if item.get_active() and not self._syncing:
            self.on_select(*key)

    def _head_toggled(self, item):
        if not self._syncing:
            self.on_select("ГОЛОВА", item.get_active())

    # --- вызывать можно из любого потока ------------------------------------
    def mode(self, state, lang=None, note=""):
        if self.ok:
            self.GLib.idle_add(self._mode, state, lang, note)

    def head_note(self, note, on):
        if self.ok:
            self.GLib.idle_add(self._head, note, on)

    # --- поток GTK -----------------------------------------------------------
    def _mode(self, state, lang, note):
        # Голова гаснет при любой смене режима, демон об этом отдельно
        # не сообщает — иначе значок сменился бы дважды, см. set_state.
        # Тот же режим — это сбой (Daemon.fail), голова при нём не гаснет.
        if state != self.state:
            self.head_on = False
            if self.head_toggle.get_active():
                self._syncing = True
                self.head_toggle.set_active(False)
                self._syncing = False
            self.head.hide()
        self.state = state
        self._icon()
        self.status.set_label(f"Режим: {state}" + (f" — {note}" if note else ""))
        item = self.radio.get((state, lang if state == "ДИКТОВКА" else None))
        if item and not item.get_active():
            self._syncing = True
            item.set_active(True)
            self._syncing = False
        return False                     # idle_add: один раз

    def _head(self, note, on):
        self.head_on = on
        self._icon()
        if self.head_toggle.get_active() != on:
            self._syncing = True
            self.head_toggle.set_active(on)
            self._syncing = False
        self.head.set_label(f"Голова: {note}")
        self.head.show()
        return False

    def _icon(self):
        if self.head_on:
            self.ind.set_icon_full(HEAD_ICON, "ГОЛОВА")
        else:
            self.ind.set_icon_full(ICONS.get(self.state, ICONS["СОН"]), self.state)
