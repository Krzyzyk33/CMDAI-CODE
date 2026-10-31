"""Pole szybkiego wyszukiwania w gornym pasku.

Jedno pole, dwie rzeczy w nim:
  - `edpp` -> lista plikow dopasowanych po rozrzuconych znakach,
  - `print hello world` -> lista miejsc, gdzie taki tekst wystepuje.

Wynik jest jedna lista, bo uzytkownik nie mysli "czy to nazwa pliku,
czy tresc" - mysli " znajdz mi to". Pliki z dopasowaniem w nazwie
 ida na gore, tresc ponizej.

Pasek ma `height: 1`, wiec `Input` musi byc bez obwodki - inaczej
zajmie 3 wiersze i rozwali pasek. To ten sam problem, ktory kiedys
dal "nie ma miejsca do pisania" przy `height: 1` + `border: round`.
"""
from __future__ import annotations

import os
from typing import List, Optional

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Static

from ..core.search import (
    FileHits, Options, SearchError, find_files_by_name, find_in_project)
from .clickable import MouseActivatable

WYNIKI_MAX_PLIKI = 8
WYNIKI_MAX_TRESCI = 60

KOLOR_PLIK = "#58a6ff"
KOLOR_TRESC = "#d29922"
KOLOR_DIM = "#6e7681"
KOLOR_PODGLAD = "#8b949e"


class QuickResult(MouseActivatable, Static):
    """One row: file match or content match. Click opens it."""

    DEFAULT_CSS = """
    /* Wiersz wyniku: wciecie, zeby nazwa pliku nie lepila sie do
       lewej krawedzi panelu, a katalog od niej. */
    QuickResult {
        width: 100%;
        height: 1;
        padding: 0 1 0 3;
        background: transparent;
        color: #c9d1d9;
    }
    QuickResult:hover {
        background: #21262d;
        color: #ffffff;
    }
    /* Zaznaczenie NIE jest jasnoniebieskim blokiem na cala
       szerokosc - `background: #1f6feb` zajal caly wiersz od lewej do
       prawej i wygladal jak pasek postepu, nie jak wybor (zgloszone
       na zrzucie). Teraz: ciemne tlo + jasnoniebieski akcent po
       lewej, jak w VS Code. */
    QuickResult.-zaznaczony {
        background: #1f3b57;
        color: #ffffff;
        border-left: solid #58a6ff;
    }
    """

    class Clicked(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def __init__(self, path: str, row: int, col: int, label: Text,
                 hint: str = "", parent_ref=None, **kwargs):
        super().__init__(label, **kwargs)
        self.path = path
        self.row = row
        self.col = col
        self.hint = hint
        self.tooltip = hint or path
        # The dropdown lives on the `Screen`, NOT inside `QuickOpen`,
        # so a `Clicked` message posted from here bubbles to the screen
        # and never reaches the top-bar widget. Measured: the click
        # neither hung nor opened the file.
        #
        # Relaying through `post_message` from the handler that is
        # currently processing the same message instance HANGS
        # (`pilot.pause()` never returns). So the dropdown is called
        # directly - a plain method call, no message, no cycle.
        self.parent_ref = parent_ref

    def on_activate(self) -> None:
        if self.parent_ref is not None:
            self.parent_ref.otworz(self.path, self.row, self.col)
            return
        self.post_message(self.Clicked(self.path, self.row, self.col))


class QuickResults(VerticalScroll):
    """The dropdown. Separate from the top bar so it can overlap the
    editor instead of pushing it down."""

    DEFAULT_CSS = """
    /* Lista jest montowana na EKRANIE i `dock: top`.
       Wczesniej kontener mial `width: 100%` + `align-horizontal:
       center`, czyli zajmowal 120 kolumn. Widoczna czesc (panel) byla
       wtedy w porzadku, ale kontener zostawal w `overlay` i przejmo-
       wal klikniecia na calej szerokosci ekranu - zmierzone: przy
       otwartej liscie `get_widget_at(5, 1)`, `(20, 1)`, `(100, 1)` i
       `(115, 1)` zwracalo `QuickResults` zamiast `#editor-tabs`,
       `#explorer-tree` i `#code-area`. Czyli lista zaslania to, co
       jest po prawej i po lewej.
       Teraz kontener jest TEJ SAMEJ szerokosci co pole, a jego `x`
       ustawia `_ustaw_geometrie` z regionu pola. Dzieki temu nic poza
       rozwijka nie jest zaslaniane ani blokowane, a szerokosc panelu
       idzie za polem automatycznie (`width: 100%`). */
    QuickResults {
        layer: overlay;
        dock: top;
        display: none;
        width: 46;
        height: auto;
        /* `dock: top` liczy sie od krawedzi EKRANU, nie od dolnej
           krawedzi paska, wiec lista ladowala na `y=0` - pod paskiem
           (zmierzone: `panel: Region(x=22, y=0, ...)`).
           `margin-top` = wysokosc paska, ktora jest teraz 1 wiersz. */
        margin-top: 1;
    }
    QuickResults.-otwarte {
        display: block;
    }
    /* Panel przejmuje cala szerokosc kontenera, ktory jest szerokosci
       pola wyszukiwania. Wczesniej panel mial `width: 76` przy polu
       `46` - o 30 kolumn za szeroki, przez co wygladal jak osobny
       panel unoszacy sie nad edytorem, a nie jak rozwijka spod pola
       (zmierzone: `panel width=76`, `pole width=46`).
       Ramka: cienka, jedna, bez niebieskiej linii na gorze - ta
       robila wrazenie osobnego okna. */
    #quick-panel {
        width: 100%;
        height: auto;
        max-height: 16;
        background: #0d1117;
        border: round #30363d;
    }
    /* Klasa, nie `id`. `Widget.remove()` jest ODROZONE - widget znika
       dopiero po refresh - wiec `remove()` dziecka i natychmiastowy
       `mount()` kolejnego o tym samym `id` konczyly sie
       `DuplicateIds` (zmierzone: drugie wyszukanie bez trafien
       wywalalo aplikacje). Klasa nie wymaga unikalnosci. */
    .-pusty {
        width: 100%;
        height: 1;
        padding: 0 1;
        color: #6e7681;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="quick-panel"):
            yield Static("", classes="-pusty")

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pozycje: List[QuickResult] = []
        self._indeks = 0
        self._oczekujace = None
        self._owner = None

    @property
    def _panel(self):
        return next(iter(self.query("#quick-panel")), None)

    def ustaw_wyniki_bez_odczeki(self, pozycje: List[QuickResult],
                                  pusty: str = "") -> None:
        """Rebuild without awaiting.

        Synchronous on purpose: this runs from `on_input_changed`, and
        an `await` there needs a worker, and a worker cancelled by the
        next keystroke leaves the list empty. `mount` on a
        `VerticalScroll` that already has children does not need
        `is_attached` - only `remove_children` on SELF does (see
        `ui/search_pane.py`), and here we are never pruned, because the
        dropdown is mounted on the `Screen`, not inside the top bar.
        """
        # `Widget.mount` raises `MountError` unless `self.is_attached`,
        # and `remove_children()` sets `_pruning` on THIS widget, which
        # briefly clears `is_attached` (measured in `ui/search_pane.py`).
        # Removing children ONE BY ONE does not prune the container, so
        # `is_attached` survives and the dropdown can be rebuilt
        # synchronously on every keystroke.
        # Usuwamy DZIECI PANELU, nie `#quick-panel` samo. Wczesniejsza
        # wersja robila `for stary in list(self.children): stary.remove()`
        # na `self`, czyli kasowala panel razem z jego zawartoscia -
        # `_panel` wracalo `None` i lista miala `height: 0`
        # (zmierzone: `children: []`, `region panel: -`).
        panel = self._panel
        if panel is None:
            self._oczekujace = ([Static(pusty, classes="-pusty")] if pusty
                                else list(pozycje))
            self.call_after_refresh(self._dokoncz)
            return
        for stary in list(panel.children):
            stary.remove()
        self._pozycje = pozycje
        self._indeks = 0
        if not self.is_attached:
            self._oczekujace = ([Static(pusty, classes="-pusty")] if pusty
                                else list(pozycje))
            self.call_after_refresh(self._dokoncz)
            return
        if pusty:
            panel.mount(Static(pusty, classes="-pusty"))
        else:
            panel.mount_all(pozycje)
        self._pokaz_pozycje()

    def _dokoncz(self) -> None:
        dzieci, self._oczekujace = self._oczekujace, None
        panel = self._panel
        if dzieci and panel is not None and self.is_attached:
            for stary in list(panel.children):
                stary.remove()
            panel.mount_all(dzieci)
            self._pokaz_pozycje()

    def _pokaz_pozycje(self) -> None:
        for i, p in enumerate(self._pozycje):
            p.set_class(i == self._indeks, "-zaznaczony")

    def przesun(self, delta: int) -> None:
        if not self._pozycje:
            return
        self._indeks = (self._indeks + delta) % len(self._pozycje)
        self._pokaz_pozycje()
        self.scroll_to_widget(self._pozycje[self._indeks], animate=False)

    @property
    def wybrany(self) -> Optional[QuickResult]:
        if not self._pozycje:
            return None
        return self._pozycje[self._indeks]

    # The dropdown relays the click. Two measurements pinned this down:
    # - re-posting the SAME `Message` instance from the handler that is
    #   processing it made `pilot.pause()` never return (a hang),
    # - doing nothing means the message bubbles to the `Screen` and
    #   `QuickOpen` (the top-bar widget) never sees it, so the click
    #   silently did nothing - the old buffer stayed open.
    # So: stop the bubbling, and post a FRESH message of our own.
    # Declared BEFORE the handler that uses it: the name in
    # `@on(QuickResults.ResultClicked)` is resolved at import time, and
    # a class defined below the decorator is a `NameError` then.
    class ResultClicked(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def otworz(self, path: str, row: int, col: int) -> None:
        """Called by `QuickResult.on_activate`.

        A direct method call all the way up, with NO messages on the
        path. Three measured attempts to do this with messages:
        1. `post_message` of the same instance from its own handler
           hangs the event loop (`pilot.pause()` never returns),
        2. `event.stop()` alone kills it - the click did nothing and
           the previously open file stayed open,
        3. the dropdown lives on the `Screen`, outside the `QuickOpen`
           subtree, so a bubbled `Clicked` never reaches the top-bar
           widget at all.
        `owner` is the `QuickOpen` passed in at mount time, so the
        chain is: QuickResult -> QuickResults -> QuickOpen -> App.
        """
        if self._owner is not None:
            self._owner.otworz_plik(path, row, col)

    def _set_owner(self, owner) -> None:
        self._owner = owner


class QuickOpen(Horizontal):
    """`Input` + `QuickResults`, living in the top bar.

    The input is borderless and one row high so it fits `EditorTopBar`
    (`height: 1`).
    """

    DEFAULT_CSS = """
    QuickOpen {
        width: 46;
        height: 1;
    }
    /* Przezroczyste pole na JEDEN wiersz.

       Wersja z `border: round` wymagala `height: 3` na pasku i
       rysowala widoczna ramke: tlo `#0d1117` na pasku `#161b22` jest
       CIEMNIEJSZE, wiec pole stawalo sie jako ciemna skrzynka
       (zmierzone: `#quick-box background: Color(13, 17, 23)`,
       `border: ('round', #30363d)`, pasek `height: 3`). Uzytkownik
       prosil o jedna linie i przezroczystosc.

       Teraz: `border: none`, `background: transparent` - pole znika
       w pasku i widac tylko tekst. Tlo pojawia sie wylacznie przy
       fokusie, zeby bylo widac gdzie sie pisze. */
    /* Pole musi byc WIDOCZNE jako pole, inaczej nie wiadomo gdzie
       pisac - przy `background: transparent` ginalo w pasku
       (zmierzone: `#quick-box background: Color(0, 0, 0, a=0)`).
       Teraz: w spoczynku cienkie tlo, przy fokusie wyraznie jasniejsze
       + niebieska lewa krawedz jako znacznik "tu piszesz". */
    #quick-box {
        width: 100%;
        height: 1;
        border: none;
        background: #21262d;
    }
    #quick-box:focus-within {
        background: #30363d;
    }
    #quick-input {
        width: 100%;
        height: 1;
        border: none;
        border-left: solid #58a6ff;
        padding: 0 1;
        background: transparent;
        color: #e6edf3;
    }
    #quick-input:focus {
        background: transparent;
    }
    """

    class Opened(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def __init__(self, root_dir: str = ".", **kwargs) -> None:
        super().__init__(**kwargs)
        self._root = os.path.abspath(root_dir)
        self._pliki = []
        self._tresci: List[FileHits] = []
        self._tekst = ""

    def set_root(self, root: str) -> None:
        self._root = os.path.abspath(root)

    def compose(self) -> ComposeResult:
        with Vertical(id="quick-box"):
            yield Input(placeholder="Search files and code", id="quick-input")

    def on_mount(self) -> None:
        # Wyniki sa montowane na EKRAN, nie jako dziecko paska.
        # `QuickOpen` ma `height: 1`, a `QuickResults` jest `dock: top`
        # - zamontowane wewnatrz pasku liczylo by sie do jego
        # wysokosci i rozpychalo `EditorTopBar` do ~19 wierszy.
        # `on_mount` rodzica (pasek) nie wystarcza: `Screen.mount`
        # wymaga, zeby ekran juz byl w drzewie.
        self.call_after_refresh(self.reparent_results)

    def reparent_results(self) -> None:
        """Create the dropdown on the screen, once, on first open."""
        istniejace = self._find_results()
        if istniejace is not None:
            istniejace._set_owner(self)
            return
        ekran = self.app.screen
        if not ekran.is_attached:
            self.call_after_refresh(self.reparent_results)
            return
        nowe = QuickResults(id="quick-results")
        nowe._set_owner(self)
        ekran.mount(nowe)

    def _find_results(self) -> Optional[QuickResults]:
        return next(iter(self.app.screen.query("#quick-results")), None)

    # --- wyniki --------------------------------------------------------

    def _zbuduj(self, tekst: str) -> List[QuickResult]:
        pozycje: List[QuickResult] = []
        wyniki = self._wyniki()
        for i, f in enumerate(self._pliki[:WYNIKI_MAX_PLIKI]):
            nazwa = Text()
            nazwa.append(os.path.basename(f.name), style="bold " + KOLOR_PLIK)
            katalog = os.path.dirname(f.name)
            if katalog:
                nazwa.append("  " + katalog, style=KOLOR_DIM)
            pozycje.append(QuickResult(f.path, 0, 0, nazwa, "open file",
                                       parent_ref=wyniki))
        licznik = sum(p.count for p in self._tresci)
        for plik in self._tresci:
            for m in plik.matches:
                if len(pozycje) >= WYNIKI_MAX_PLIKI + WYNIKI_MAX_TRESCI:
                    break
                wiersz = Text()
                wiersz.append(f"{m.row + 1:>4}  ", style=KOLOR_DIM)
                podglad = m.preview.strip()
                start = podglad.lower().find(tekst.lower()) if tekst else -1
                if 0 <= start < len(podglad):
                    koniec = min(start + len(tekst), len(podglad))
                    wiersz.append(podglad[:start], style=KOLOR_PODGLAD)
                    wiersz.append(podglad[start:koniec],
                                  style="bold " + KOLOR_TRESC)
                    wiersz.append(podglad[koniec:], style=KOLOR_PODGLAD)
                else:
                    wiersz.append(podglad, style=KOLOR_PODGLAD)
                sciezka = os.path.relpath(plik.path, self._root)
                pozycje.append(QuickResult(plik.path, m.row, m.col, wiersz,
                                           f"{sciezka}:{m.row + 1}",
                                           parent_ref=wyniki))
        return pozycje

    def _wyniki(self) -> Optional[QuickResults]:
        """The dropdown, wherever it currently lives."""
        return next(iter(self.app.screen.query("#quick-results")), None)

    def _ustaw_geometrie(self) -> None:
        """Pin the dropdown to the field's column.

        The container is docked to the screen, so without this it starts
        at x=0. Measured before: `get_widget_at(5, 1)` returned
        `QuickResults` instead of `#explorer-tree` - the full-width
        container in the `overlay` layer swallowed every click across
        the whole terminal, not just over the dropdown.
        """
        wyniki = self._wyniki()
        if wyniki is None:
            return
        pole = self._pole
        szerokosc = max(pole.outer_size.width, 8)
        wyniki.styles.width = szerokosc
        # `offset`, nie `margin_left` - zmierzone na dokowanym widgetcie:
        # `margin_left = 37` dalo `panel x=0`, a `offset = (37, 0)` dalo
        # `panel x=37`. `padding_left` tez nie rusza (`x=0`).
        wyniki.styles.offset = (max(pole.region.x, 0), 0)

    def _przelicz_sync(self) -> None:
        tekst = self._tekst.strip()
        wyniki = self._wyniki()
        if wyniki is None:
            return
        self._ustaw_geometrie()
        if not tekst:
            wyniki.ustaw_wyniki_bez_odczeki([])
            wyniki.remove_class("-otwarte")
            self._pliki = []
            self._tresci = []
            return
        self._pliki = find_files_by_name(self._root, tekst,
                                         limit=WYNIKI_MAX_PLIKI)
        self._tresci = []
        if not self._pliki:
            # Nic po nazwie - szukamy w tresci.
            try:
                self._tresci, _, _ = find_in_project(
                    self._root, tekst, Options(), limit=WYNIKI_MAX_TRESCI)
            except SearchError:
                wyniki.ustaw_wyniki_bez_odczeki([], f"Bad regex: {tekst}")
                wyniki.add_class("-otwarte")
                return
        pozycje = self._zbuduj(tekst)
        pusty = "" if pozycje else f"No matches for '{tekst}'"
        wyniki.ustaw_wyniki_bez_odczeki(pozycje, pusty)
        wyniki.add_class("-otwarte")

    # --- sterowanie -----------------------------------------------------

    @property
    def _pole(self) -> Input:
        return self.query_one("#quick-input", Input)

    def focus_field(self, wstep: str = "") -> None:
        """Open the field, replacing whatever was in it.

        Assigning unconditionally is deliberate: `Ctrl+Shift+P` with
        the word `print` under the cursor used to APPEND to a leftover
        value, and the measured result was
        "No matches for 'print('hello world')zzzq" - three separate
        queries spliced together.
        """
        self.reparent_results()
        self._pole.value = wstep
        self._pole.focus()
        self._tekst = wstep
        self._ustaw_geometrie()
        self._przelicz_teraz()

    def on_resize(self) -> None:
        self._ustaw_geometrie()

    def close(self) -> None:
        self._pole.value = ""
        self._tekst = ""
        wyniki = self._wyniki()
        if wyniki is not None:
            wyniki.remove_class("-otwarte")

    BINDINGS = [
        # `Binding(..., priority=True)` uruchamia akcje PRZED
        # wskazanym widgetem. `Input` wiaze `up`/`down` dla wlasnej
        # historii i zjada je - bez tego zmierzone:
        # `pilot.press("down")` zostawial zaznaczenie na tym samym
        # wierszu (`app.py` przed i po). To jest ten sam mechanizm,
        # ktory edytor uzywa dla `ctrl+w` (kolidacja z
        # `TextArea.delete_word_left`).
        #
        # `BINDINGS` musi byc krotka 2-3 napisow LUB obiektow
        # `Binding` - czwarty element w krotce wywala `BindingError`
        # przy imporcie klasy.
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("pagedown", "cursor_page_down", "Page Down", priority=True),
        Binding("pageup", "cursor_page_up", "Page Up", priority=True),
    ]

    def action_cursor_down(self) -> None:
        self.move_selection(1)

    def action_cursor_up(self) -> None:
        self.move_selection(-1)

    def action_cursor_page_down(self) -> None:
        self.move_selection(5)

    def action_cursor_page_up(self) -> None:
        self.move_selection(-5)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._otworz_wybrany()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._tekst = event.value
        # `Input.Changed.value` to stan w chwili zdarzenia, a
        # `self._pole.value` to stan TERAZ. Jesli uzytkownik pisze
        # szybko, kolejne zdarzenia moga zostac scalone, a wtedy
        # `event.value` jest nieaktualne: zmierzony wynik to
        # "No matches for 'print('hello world')x'" - z klamra
        # poprzedniej wersji i `x` z biezacej. Czytamy pole, nie
        # zdarzenie.
        self._tekst = self._pole.value
        self._przelicz_teraz()

    def _przelicz_teraz(self) -> None:
        """Recompute the result list.

        NO worker, deliberately. `run_worker(..., exclusive=True,
        group=...)` cancels the previous worker on every keystroke, and
        a cancelled worker never reaches its `await` points, so the
        list stayed EMPTY - measured: 0 results for `e`, `ed`, `edp`
        and `edpp` alike, with no hang. Two earlier attempts, both
        measured: `call_after_refresh` with a coroutine never runs it
        (it wants a callable), and `run_worker` defaults to a thread
        where a coroutine has no event loop (`KeyError:
        '__import__'`).

        `find_files_by_name` is a directory walk, 18 ms on a
        174-file repo, so doing it inline on a keystroke is fine and
        keeps the list in step with the text.
        """
        self._przelicz_sync()

    async def _przelicz(self) -> None:
        await self._przelicz_sync()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._otworz_wybrany()

    @on(QuickResults.ResultClicked)
    def _on_result(self, event: QuickResults.ResultClicked) -> None:
        event.stop()
        self.otworz_plik(event.path, event.row, event.col)

    def otworz_plik(self, path: str, row: int, col: int) -> None:
        """Emit `Opened` for the app. Called directly, see
        `QuickResults.otworz` for why no message is used here."""
        self.post_message(self.Opened(path, row, col))

    def _otworz_wybrany(self) -> None:
        wyniki = self._wyniki()
        if wyniki is None:
            return
        wynik = wyniki.wybrany
        if wynik is None:
            return
        self.post_message(self.Opened(wynik.path, wynik.row, wynik.col))

    def move_selection(self, delta: int) -> None:
        wyniki = self._wyniki()
        if wyniki is not None:
            wyniki.przesun(delta)
