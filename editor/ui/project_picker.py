"""Ekran PROJECTS - pelny ekran, dwie kolumny, z usuwaniem.

Uklad:

  LEWA (2fr)                       PRAWA (3fr)
  +--------------------------------+--------------------------------+
  | EXISTING                       | NEW PROJECT                     |
  |                                | + 📁 name    [______] [create]  |
  | 📁 alpha   3 files             |                                |
  | 📁 beta    1 file              | 📂 open a folder                |
  |                                |    [______________]    [open]  |
  | [remove selected]              |                                |
  +--------------------------------+--------------------------------+
  arrows = move   Enter = open   Del = remove   Esc = close
"""
from __future__ import annotations

import os
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..core.workspace import (
    create_project,
    delete_project,
    list_projects,
    project_size,
    projects_root,
)

ICON_FOLDER = "\U0001f4c1"
ICON_NEW = "+"
ICON_OPEN = "\U0001f4c2"
ICON_DEL = "\u2715"

STYL_CZEGOLOWY = "#c9d1d9"
STYL_SLABY = "#6e7681"
STYL_PANEL = "#0d1117"


class ProjectPicker(ModalScreen[Optional[str]]):
    """Zwraca absolutna sciezke projektu albo `None` (Esc = anuluj)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("delete", "ask_delete", "Remove"),
        Binding("ctrl+delete", "ask_delete", "Remove"),
    ]

    DEFAULT_CSS = """
    ProjectPicker {
        align: center middle;
        background: #010409;
    }
    #pr-box {
        width: 96%;
        height: 92%;
        background: #0d1117;
        border: round #30363d;
        padding: 0 1;
    }

    /* --- naglowek ---------------------------------------------------- */
    #pr-head {
        height: 3;
        background: #161b22;
        padding: 0 1;
        border-bottom: solid #30363d;
    }
    #pr-title {
        color: #58a6ff;
        text-style: bold;
    }
    #pr-path {
        color: #6e7681;
    }

    /* --- kolumny ---------------------------------------------------- */
    #pr-cols {
        height: 1fr;
    }
    #pr-left {
        width: 2fr;
        height: 100%;
        border-right: solid #30363d;
        padding: 0 1;
    }
    #pr-right {
        width: 3fr;
        height: 100%;
        padding: 0 1;
    }

    /* --- tytuly kolumn ---------------------------------------------- */
    .pr-col-title {
        height: 1;
        text-style: bold;
    }
    #pr-left-title {
        color: #d29922;
    }
    #pr-right-title {
        color: #3fb950;
    }

    /* --- lista projektow -------------------------------------------- */
    #pr-list-slot {
        height: 1fr;
    }
    #pr-list {
        height: 1fr;
        background: #0d1117;
        border: none;
        scrollbar-size-vertical: 1;
        scrollbar-color: #30363d;
    }
    #pr-list:focus {
        border: none;
    }
    #pr-list > .option-list--option-highlighted {
        background: #1f6feb;
        color: #ffffff;
    }
    #pr-empty {
        color: #6e7681;
        height: auto;
        padding: 1 0;
    }
    /* Stan pusty realizowany KLASA, nie `display = False` - powod
       w `display: none` przepada po ponownym zamontowaniu widgetu. */
    #pr-list-slot > .-hidden {
        display: none;
    }

    /* --- przyciski: zwarte, nie pelnoszerokie pasy -------------------- */
    .pr-btn {
        height: 3;
        min-width: 12;
        background: #21262d;
        border: none;
        color: #c9d1d9;
        margin-left: 1;
    }
    .pr-btn:focus {
        background: #1f6feb;
        color: #ffffff;
        text-style: bold;
    }
    .pr-btn.-danger:focus {
        background: #da3633;
    }
    .pr-btn.-danger {
        color: #f85149;
    }
    #pr-create {
        background: #238636;
        color: #ffffff;
    }
    #pr-create:focus {
        background: #2ea043;
    }
    #pr-remove {
        width: 100%;
        margin-left: 0;
        margin-top: 1;
    }

    /* --- formularze -------------------------------------------------- */
    #pr-form {
        height: auto;
    }
    .pr-label {
        color: #8b949e;
        height: 1;
        margin-top: 1;
    }
    .pr-input {
        height: 3;
        border: round #30363d;
        background: #0d1117;
    }
    .pr-input:focus {
        border: round #58a6ff;
    }
    #pr-name, #pr-pathin {
        width: 1fr;
    }
    .pr-row {
        height: 3;
    }

    /* --- pasek dolny ------------------------------------------------- */
    #pr-foot {
        height: 1;
        color: #6e7681;
        background: #161b22;
        padding: 0 1;
    }
    #pr-error {
        height: 1;
        color: #f85149;
        padding: 0 1;
    }
    #pr-error:empty {
        height: 0;
    }
    #pr-confirm {
        height: auto;
        padding: 0 1;
    }
    #pr-confirm.-hidden {
        display: none;
    }
    #pr-confirm-box {
        height: auto;
        border: round #da3633;
        background: #1c1416;
        padding: 0 1;
    }
    #pr-confirm-text {
        color: #f85149;
        height: auto;
    }
    #pr-yes, #pr-no {
        height: 3;
        margin-top: 1;
    }
    #pr-yes {
        width: 1fr;
        background: #da3633;
        color: #ffffff;
        margin-right: 1;
    }
    #pr-no {
        width: 1fr;
        background: #21262d;
        color: #c9d1d9;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._projects = list_projects()
        self._zaznaczony: Optional[str] = None
        self._potwierdzenie: Optional[str] = None

    # --- uklad ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="pr-box"):
            with Vertical(id="pr-head"):
                yield Static("CMDAI EDITOR  -  projects", id="pr-title")
                yield Static(f"projects: {projects_root()}", id="pr-path")
            with Horizontal(id="pr-cols"):
                with Vertical(id="pr-left"):
                    yield Static("EXISTING", id="pr-left-title",
                                 classes="pr-col-title")
                    # Cala lista mieszka w `#pr-list-slot`. Po usunieciu
                    # projektu robimy `remove_children()` na tym jednym
                    # kontenerze i montujemy swiezy widget. Wczesniejsza
                    # wersja robila `stary.remove()` + `mount()` w
                    # `#pr-left`, co konczalo sie `AppError: Can't
                    # register` - `remove()` jest asynchroniczne, wiec
                    # stary `#pr-list` byl jeszcze w rejestrze, gdy
                    # pojawial sie nowy o tym samym id.
                    with Vertical(id="pr-list-slot"):
                        yield self._panel_listy()
                    with Vertical(id="pr-confirm", classes="-hidden"):
                        yield Static("", id="pr-confirm-text")
                        with Horizontal(id="pr-confirm-row"):
                            yield Button("remove", id="pr-yes")
                            yield Button("keep", id="pr-no")
                    b = Button(f"{ICON_DEL}  remove selected", id="pr-remove",
                               classes="pr-btn -danger")
                    b.tooltip = "Delete the highlighted project (Del)"
                    yield b
                with Vertical(id="pr-right"):
                    yield Static("NEW PROJECT", id="pr-right-title",
                                 classes="pr-col-title")
                    with Vertical(id="pr-form"):
                        yield Label(f"{ICON_NEW} {ICON_FOLDER}  "
                                    "create a project in projects/",
                                    id="pr-label-1", classes="pr-label")
                        with Horizontal(id="pr-row-1", classes="pr-row"):
                            yield Input(placeholder="moj-projekt",
                                        id="pr-name", classes="pr-input")
                            yield Button("create", id="pr-create",
                                         classes="pr-btn")
                        yield Label(f"{ICON_OPEN}  or open an "
                                    "existing folder", id="pr-label-2",
                                    classes="pr-label")
                        with Horizontal(id="pr-row-2", classes="pr-row"):
                            yield Input(placeholder=r"C:\Users\...\projekt",
                                        id="pr-pathin", classes="pr-input")
                            yield Button("open", id="pr-open",
                                         classes="pr-btn")
                    yield Static("", id="pr-error")
            yield Static("", id="pr-foot")

    def _panel_listy(self):
        if self._projects:
            return self._build_list()
        return Static("", id="pr-list-wrap")

    def _opcje(self) -> list[Option]:
        """Pozycje listy: nazwa + liczba plikow.

        Osobna metoda, bo `OptionList` pozwalasz odswiezyc przez
        `clear_options().add_options(...)` bez montowania nowego
        widgetu - `remove_children()` + `mount()` w jednej chwili
        konczylo sie `DuplicateIds` dla `pr-list`.
        """
        opts: list[Option] = []
        for p in self._projects:
            n = project_size(p)
            label = Text()
            label.append(f"{ICON_FOLDER} ", style="#d29922")
            label.append(p.name, style="bold #c9d1d9")
            licznik = "?" if n < 0 else (
                f"{n} file" + ("s" if n != 1 else ""))
            label.append(f"   {licznik}", style="#6e7681")
            opts.append(Option(label, id=str(p)))
        return opts

    def _build_list(self) -> OptionList:
        return OptionList(*self._opcje(), id="pr-list")

    def on_mount(self) -> None:
        self._odswiez_stopke()
        self._ustaw_fokus()

    def _ustaw_fokus(self) -> None:
        """Fokus na liscie jak sa projekty, w przeciwnym razie na polu
        nazwy - inaczej w pustym projekcie klawiatura nigdzie nie trafia."""
        if self._potwierdzenie is not None:
            self._safe("#pr-yes").focus()
        elif self._projects:
            self._safe("#pr-list").focus()
        else:
            self._safe("#pr-name").focus()

    def _odswiez_stopke(self) -> None:
        if self._potwierdzenie is not None:
            tekst = "y / n = answer    Esc = cancel"
        elif self._zaznaczony:
            tekst = (f"arrows = move   Enter = open   Del = remove "
                     f"({os.path.basename(self._zaznaczony)})   Esc = close")
        else:
            tekst = "arrows = move   Enter = open   Esc = close"
        self.query_one("#pr-foot", Static).update(tekst)

    def _safe(self, selektor: str):
        try:
            return self.query_one(selektor)
        except Exception:
            return None

    def _find(self, selektor: str):
        """Widget w dowolnym potomku, albo `None` gdy go nie ma.

        `query(selektor)` zwraca `DOMQuery`, a `.first()` w Textualu
        8.2.7 **nie przyjmuje argumentu `default`** i rzuca
        `NoMatches` na pustym query (patrz `_first()` w `ui/app.py`).
        Stad `next(iter(...), None)`.
        """
        return next(iter(self.query(selektor)), None)

    def _find_in(self, rodzic, selektor: str):
        return next(iter(rodzic.query(selektor)), None)

    # --- akcje ---------------------------------------------------------

    def action_cancel(self) -> None:
        if self._potwierdzenie is not None:
            self._ukryj_potwierdzenie()
            return
        self.dismiss(None)

    def action_ask_delete(self) -> None:
        if self._potwierdzenie is not None:
            return
        # `self._zaznaczony` ustawia `OptionHighlighted`, ktore Textual
        # wysyla TYLKO przy ruchu kursora. Po `clear_options()` +
        # `add_options()` zaznaczenie jest resetowane, wiec klawisz Del
        # przestal dzialac cicho - dlatego bierzemy identyfikator
        # wprost z `OptionList.highlighted`, a nie z zapamietanego pola.
        cel = self._zaznaczony_lub_kursor()
        if not cel:
            return
        self._zaznaczony = cel
        self._potwierdzenie = cel
        nazwa = os.path.basename(cel)
        self.query_one("#pr-confirm-text", Static).update(
            f"{ICON_DEL}  remove '{nazwa}' and everything inside it?")
        self.query_one("#pr-confirm").remove_class("-hidden")
        self._odswiez_stopke()
        self._ustaw_fokus()

    def _zaznaczony_lub_kursor(self) -> Optional[str]:
        """Co jest zaznaczone TERAZ: zapamietane pole, a jak puste -
        aktualna pozycja kursora na liscie.

        Po usunieciu projektu `clear_options()` zeruje zaznaczenie, a
        `OptionHighlighted` nie zostaloby wysłany, wiec samo
        `self._zaznaczony` dawalo `None` i `Del` nie robil nic.
        """
        if self._zaznaczony:
            return self._zaznaczony
        lista = self._find("#pr-list")
        if lista is None:
            return None
        opcja = lista.highlighted
        return str(opcja.id) if opcja is not None and opcja.id else None

    def _ukryj_potwierdzenie(self) -> None:
        self._potwierdzenie = None
        self.query_one("#pr-confirm").add_class("-hidden")
        self._odswiez_stopke()
        self._ustaw_fokus()

    def _potwierdz_usuniecie(self) -> None:
        cel = self._potwierdzenie
        if cel is None:
            return
        nazwa = os.path.basename(cel)
        if delete_project(cel):
            self._error("")
            self._projects = list_projects()
            self._zaznaczony = None
            self._ukryj_potwierdzenie()
            self._przebuduj_liste()
            self._error(f"removed '{nazwa}'")
        else:
            self._error(f"cannot remove '{nazwa}'")

    def _przebuduj_liste(self) -> None:
        """Odświeża zawartość `#pr-list-slot`.

        `remove_children()` + `mount()` w TEJ SAMEJ chwili nie działa
        w Textualu 8.2.7 - `remove_children()` jest odłożone, a
        `mount()` natychmiast, więc `_node_list._ensure_unique_id()`
        widzi stary `pr-list` w rejestrze i rzuca `DuplicateIds`.
        Dlatego `OptionList` obsługujemy przez jego własne API
        (`clear_options()` + `add_options()`), a stan „brak projektów”
        realizujemy **klasą CSS**, nie `display = False`.

        Dlaczego klasa, a nie `display`: `display=False` ustawia
        `styles.display`, ale po zdjęciu i ponownym zamontowaniu
        widgetu klasa nigdy nie była dodana, więc `remove_class()`
        nic nie przywraca - i komunikat o pustym stanie znikał na
        stałe (`widac komunikat 'No projects yet'` padało).
        """
        slot = self.query_one("#pr-list-slot", Vertical)
        lista = self._find_in(slot, "#pr-list")
        pusty = self._find_in(slot, "#pr-empty")

        if not self._projects:
            if lista is not None:
                lista.add_class("-hidden")
            if pusty is None:
                slot.mount(Static(
                    "No projects yet - create one on the right, "
                    "or open an existing folder.",
                    id="pr-empty", classes="-shown"))
            return

        if lista is None:
            slot.mount(self._build_list())
            return
        lista.remove_class("-hidden")
        if pusty is not None:
            pusty.add_class("-hidden")
        lista.clear_options().add_options(self._opcje())

    def _error(self, msg: str) -> None:
        self.query_one("#pr-error", Static).update(msg)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        event.stop()
        self._zaznaczony = event.option.id
        self._odswiez_stopke()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        if event.option.id:
            self.dismiss(os.path.abspath(str(event.option.id)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        ktore = event.button.id
        if ktore == "pr-create":
            self._utworz()
        elif ktore == "pr-open":
            self._otworz_folder()
        elif ktore == "pr-remove":
            self.action_ask_delete()
        elif ktore == "pr-yes":
            self._potwierdz_usuniecie()
        elif ktore == "pr-no":
            self._ukryj_potwierdzenie()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "pr-name":
            self._utworz()
        elif event.input.id == "pr-pathin":
            self._otworz_folder()

    # --- prawa kolumna ------------------------------------------------

    def _utworz(self) -> None:
        nazwa = self.query_one("#pr-name", Input).value.strip()
        self._error("")
        if not nazwa:
            self._error("Type a project name first.")
            self.query_one("#pr-name", Input).focus()
            return
        projekt = create_project(nazwa)
        if projekt is None:
            if os.path.isdir(projects_root() / nazwa):
                self._error(f"'{nazwa}' already exists in projects/.")
            else:
                self._error(
                    f"Cannot use that name: {nazwa}. Letters, digits, - _ . "
                    "and spaces; no CON/NUL/COM1, no leading dot.")
            self.query_one("#pr-name", Input).focus()
            return
        self.dismiss(str(projekt))

    def _otworz_folder(self) -> None:
        wartosc = self.query_one("#pr-pathin", Input).value.strip().strip('"')
        self._error("")
        if not wartosc:
            self._error("Type a folder path first.")
            self.query_one("#pr-pathin", Input).focus()
            return
        sciezka = os.path.abspath(os.path.expanduser(wartosc))
        if not os.path.isdir(sciezka):
            self._error(f"Not a folder: {sciezka}")
            self.query_one("#pr-pathin", Input).focus()
            return
        self.dismiss(sciezka)
