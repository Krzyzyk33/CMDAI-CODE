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

    DEFAULT_CSS = """ProjectPicker {
        background: #090d13;
    }

    /* PULAPKA NA SPECYFICZNOSC - czytac przed dodawaniem reguly na
       `.pr-btn`.

       `Button.DEFAULT_CSS` w Textualu 8.2.7 jest zagniezdzone:
       `Button { &.-style-default { ... } }` kompiluje sie do
       `Button.-style-default`, a `Button:ansi.-style-default` do
       selektora z pseudo-klasa. Kazdy z nich ma specyficznosc
       typ + klasa = 11, a pojedyncza klasa `.pr-btn` ma 10.

       Kaskada wygrywa po specyficznosci, NIE po kolejnosci, wiec
       `.pr-btn { background: ...; border: ... }` przegrywalo cicho z
       `$surface` / `$border-blurred` z motywu. Potwierdzone pomiarem
       na zywej sesji: `min-width` z `.pr-btn` byl widoczny (bo
       `min-width` lezy na samym `Button {`, czyli 1), a
       `background` i `border` - nie, i to mimo wygranej na
       `min-width` w tej samej regule.

       Dlatego kazda regula na przycisk ponizej jest po ID. */
    #pr-box {
        width: 100%;
        height: 100%;
        background: #090d13;
    }

    /* --- naglowek: 1 wiersz, jak `EditorTopBar` --------------------- */
    #pr-head {
        height: 1;
        background: #161b22;
        padding: 0 1;
    }
    #pr-title {
        width: auto;
        height: 1;
    }
    #pr-sub {
        width: auto;
        height: 1;
        color: #8b949e;
        margin-left: 1;
    }
    #pr-head-gap {
        width: 1fr;
        height: 1;
    }
    #pr-path {
        width: auto;
        height: 1;
        color: #6e7681;
    }

    #pr-cols {
        height: 1fr;
    }
    #pr-left {
        width: 2fr;
        height: 100%;
        border-right: solid #21262d;
        padding: 0 1;
    }
    #pr-right {
        width: 3fr;
        height: 100%;
        padding: 0 2;
    }

    /* Kolor zostaje tylko dla statusu i bledu. Zwykly naglowek
       kolumny wyglada jak `PanelToggle`: `height: 1`, `#8b949e`. */
    .pr-col-title {
        height: 1;
        color: #8b949e;
    }

    /* `background: transparent` - lista miala wlasne tlo, a przy
       `height: 1fr` ~300 px pustki rysowalo sie jako ciemny
       prostokat. Bez niego pusto jest po prostu tlem aplikacji. */
    #pr-list-slot {
        height: 1fr;
    }
    #pr-list {
        height: 1fr;
        background: transparent;
        border: none;
        scrollbar-size-vertical: 1;
        scrollbar-color: #21262d;
        scrollbar-background: #090d13;
    }
    #pr-list:focus {
        border: none;
    }
    #pr-list > .option-list--option-highlighted {
        background: #1f6feb;
        color: #e6edf3;
        text-style: none;
    }
    #pr-empty {
        color: #6e7681;
        height: auto;
        padding: 1 1;
    }
    #pr-list-slot > .-hidden {
        display: none;
    }

    /* --- przyciski: slownik przeniesiony 1:1 z `ui/app.py` -------- */
    #pr-create, #pr-open {
        height: 3;
        min-width: 18;
        border: round #30363d;
        background: #0d1117;
        color: #c9d1d9;
        margin-left: 1;
    }
    #pr-create {
        border: round #238636;
        background: #238636;
        color: #ffffff;
    }
    #pr-create:focus {
        border: round #2ea043;
        background: #2ea043;
    }
    #pr-open:focus {
        border: round #58a6ff;
        background: #21262d;
    }

    /* Usuwanie to akcja drugorzedna i destrukcyjna - nie moze byc
       glosniejsza niz `create`. Cichy element 1-wierszowy w stylu
       `PanelToggle`: bez tla, bez obwodki, szary, czerwieniec
       dopiero po najechaniu. `line-pad: 0` jest wymagane - `Button`
       ma `line-pad: 1`, przez co `height: 1` bez tego by niczego
       nie pokazalo. */
    #pr-remove {
        width: auto;
        min-width: 0;
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
        padding: 0 1;
        border: none;
        background: transparent;
        color: #6e7681;
        text-style: none;
    }
    #pr-remove:hover {
        background: #21262d;
        color: #f85149;
    }
    #pr-remove:focus {
        background: #21262d;
        color: #f85149;
        text-style: bold;
    }

    #pr-form {
        height: auto;
    }
    .pr-label {
        color: #8b949e;
        height: 1;
        margin-top: 2;
    }
    /* Pierwsza etykieta przykleja sie do nadania kolumny - `margin-top`
       z `.pr-label` odpycha ja o 2 wiersze od tytulu, przez co
       formularz wisial w pustce. */
    #pr-label-1 {
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

    /* --- pasek dolny: jak `EditorStatusBar` ----------------------- */
    #pr-foot {
        height: 1;
        color: #6e7681;
        background: #0d1117;
        border-top: solid #21262d;
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

    /* --- potwierdzenie usuniecia ---------------------------------- */
    #pr-confirm {
        height: auto;
        border: round #da3633;
        background: #1c1416;
        padding: 0 1;
    }
    #pr-confirm.-hidden {
        display: none;
    }
    #pr-confirm-text {
        color: #f85149;
        height: auto;
        margin-top: 1;
    }
    #pr-confirm-row {
        height: 3;
        margin-top: 1;
    }
    #pr-yes, #pr-no {
        width: 1fr;
        height: 3;
        border: round #30363d;
        background: #0d1117;
        color: #c9d1d9;
    }
    #pr-yes {
        border: round #da3633;
        background: #da3633;
        color: #ffffff;
        margin-right: 1;
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
            with Horizontal(id="pr-head"):
                yield Static(
                    Text.from_markup("[bold #58a6ff]⌬ CMDAI[/] "
                                     "[bold #ffffff]EDITOR[/]"),
                    id="pr-title")
                yield Static("projects", id="pr-sub")
                yield Static("", id="pr-head-gap")
                yield Static(str(projects_root()), id="pr-path")
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
                    b = Button(f"{ICON_DEL}  remove selected",
                               id="pr-remove")
                    # `line_pad` NIE jest zarejestrowana nazwa CSS w
                    # Textualu 8.2.7 - parser odrzuca kazda wartosc
                    # ("An integer value is expected here"), mimo ze
                    # `Button` ma w domyslnych stylach `line-pad: 1`.
                    # Bez tego `height: 1` skroci label do zera.
                    b.styles.line_pad = 0
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
                            yield Button("create", id="pr-create")
                        yield Label(f"{ICON_OPEN}  or open an "
                                    "existing folder", id="pr-label-2",
                                    classes="pr-label")
                        with Horizontal(id="pr-row-2", classes="pr-row"):
                            yield Input(placeholder=r"C:\Users\...\projekt",
                                        id="pr-pathin", classes="pr-input")
                            yield Button("open", id="pr-open")
                    yield Static("", id="pr-error")
            yield Static("", id="pr-foot")

    def _panel_listy(self):
        """Widget mounted in the slot when the screen opens.

        The empty state needs the `pr-empty` id - the same one used in
        `_przebuduj_liste()`. It used to be `Static("",
        id="pr-list-wrap")`, so opening the screen with an empty
        `projects/` showed no message at all.
        """
        if self._projects:
            return self._build_list()
        return Static("No projects yet - create one on the right, "
                      "or open an existing folder.", id="pr-empty")

    def _opcje(self) -> list[Option]:
        """Pozycje listy: nazwa + liczba plikow.

        Osobna metoda, bo `OptionList` pozwala odswiezyc przez
        `clear_options().add_options(...)` bez montowania nowego
        widgetu - `remove_children()` + `mount()` w jednej chwili
        konczylo sie `DuplicateIds` dla `pr-list`.

        Nazwy dostaja wspolna szerokosc (`ljust`), bo licznik plikow
        jest doklejany zaraz za nazwa. Bez wyrownania `2 files` i
        `1 file` startowaly w roznych kolumnach, co na zrzucie
        wygladalo jak losowe wciecia. `OptionList` nie ma kolumn, wiec
        dopelnienie spacjami jest jedynym sposobem.
        """
        najdluzsze = min(
            max((len(p.name) for p in self._projects), default=0), 30)
        opts: list[Option] = []
        for p in self._projects:
            n = project_size(p)
            label = Text()
            label.append(f"{ICON_FOLDER} ", style="#d29922")
            label.append(p.name.ljust(najdluzsze), style="bold #c9d1d9")
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
            f"{ICON_DEL}  remove '{nazwa}'?\n"
            f"This deletes everything inside it.")
        self.query_one("#pr-confirm").remove_class("-hidden")
        self._odswiez_stopke()
        self._ustaw_fokus()

    def _zaznaczony_lub_kursor(self) -> Optional[str]:
        """What is selected RIGHT NOW: the remembered field, or - if
        empty - the current cursor position on the list.

        After a project is removed `clear_options()` resets the
        selection and `OptionHighlighted` is not sent, so
        `self._zaznaczony` alone was `None` and `Del` did nothing.
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
        """Refresh the contents of `#pr-list-slot`.

        `remove_children()` + `mount()` at the same moment does not
        work in Textual 8.2.7 - `remove_children()` is deferred while
        `mount()` is immediate, so `_node_list._ensure_unique_id()`
        still sees the old `pr-list` and raises `DuplicateIds`.
        So `OptionList` is refreshed through its own API
        (`clear_options()` + `add_options()`) and the "no projects"
        state is a CSS class, not `display = False`.

        Both mechanisms are needed, not one. `display = False` alone
        is lost when a widget is removed and mounted again, because
        the class was never added - the empty-state message then
        disappeared for good. The class alone is not enough either:
        a `Static` mounted by `compose()` starts without `-hidden`,
        so `add_class()` leaves `display` already `True` and the
        message stays on screen next to the list.
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
                    "or open an existing folder.", id="pr-empty"))
            return

        if pusty is not None:
            # Both mechanisms are required. A `Static` mounted by
            # `compose()` starts without `-hidden`, so `add_class()`
            # alone leaves `display` already `True` and the message
            # stays visible next to the list.
            pusty.display = False
            pusty.add_class("-hidden")

        if lista is None:
            slot.mount(self._build_list())
            return
        lista.display = True
        lista.remove_class("-hidden")
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
