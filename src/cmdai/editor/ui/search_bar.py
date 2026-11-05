"""Pasek wyszukiwania i zamiany w biezacym pliku (Ctrl+F / Ctrl+H).

Trzyma wlasny stan wyszukiwania i sam operuje na `TextArea`: ustawia
zaznaczenie, maluje trafienia, podmienia. Dzieje sie tak z trzech
powodow - nie ma `TextArea.search` (potwierdzone: `TextArea` w
Textualu 8.2.7 nie ma ani `search`, ani `find`), wiec i tak trzeba
napisac warstwe wyzej; trzymanie tego w jednym miejscu oznacza, ze
licznik, podswietlenie i zaznaczenie nie moga sie rozjechac.

Rozmieszczenie:

    [ szukaj...              ]  3 of 17   Aa  ab  .*   ^  v  x
    [ zamien na...           ]              Replace  Replace All
"""
from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, Static
from textual.widgets.text_area import Selection

from ..core.search import (
    Match, Options, SearchError, find_in_text, replace_all)
from .clickable import MouseActivatable

MATCH_CURRENT_BG = "#2b5c8a"

IK_GORA = "\u2191"
IK_DOL = "\u2193"
IK_ZAMKNIJ = "\u2715"


class Toggle(MouseActivatable, Static):
    """`Aa` / `ab` / `.*` - przelaczniki opcji wyszukiwania.

    `MouseActivatable`, nie `Button`: przycisk w terminalu musi miec
    `height: 3` i `line-pad: 1`, a to robi z niego 3-wierszowy blok.
    Przelacznik jest 1 wierszem, klikalnym i mozna go tez obsluzyc
    klawiatura (Alt+C / Alt+W / Alt+R) bez myszy.
    """

    DEFAULT_CSS = """
    Toggle {
        width: auto;
        height: 1;
        min-width: 3;
        padding: 0 1;
        background: transparent;
        color: #6e7681;
        text-style: none;
    }
    Toggle:hover {
        background: #21262d;
        color: #ffffff;
    }
    Toggle.-on {
        background: #1f6feb;
        color: #ffffff;
        text-style: bold;
    }
    """

    class Clicked(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, label: str, klucz: str, tooltip: str = "", **kwargs):
        # `name` NIE moze byc atrybutem: `Static.name` to wlasciwosc
        # tylko do odczytu (`property 'name' has no setter`), wiec
        # `self.name = ...` wywalalo AttributeError przy compose.
        # Uzywamy `klucz`.
        super().__init__(label, **kwargs)
        self.klucz = klucz
        self.tooltip = tooltip

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.klucz))

    def set_on(self, on: bool) -> None:
        self.set_class(on, "-on")


class BarButton(MouseActivatable, Static):
    """Cichy przycisk 1-wierszowy: `^`, `v`, `x`, Replace, Replace All."""

    DEFAULT_CSS = """
    BarButton {
        width: auto;
        height: 1;
        padding: 0 1;
        background: transparent;
        color: #8b949e;
        text-style: none;
    }
    BarButton:hover {
        background: #21262d;
        color: #ffffff;
    }
    BarButton:disabled {
        color: #484f58;
    }
    """

    class Clicked(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, label: str, klucz: str, tooltip: str = "", **kwargs):
        # Jak w `Toggle` - `Static.name` jest tylko do odczytu.
        super().__init__(label, **kwargs)
        self.klucz = klucz
        self.tooltip = tooltip

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.klucz))

    def set_enabled(self, enabled: bool) -> None:
        self.disabled = not enabled


class FindReplaceBar(Vertical):

    DEFAULT_CSS = """
    FindReplaceBar {
        dock: top;
        height: auto;
        background: #161b22;
        border-bottom: solid #30363d;
    }
    FindReplaceBar.hidden {
        display: none;
    }
    #find-row {
        height: 3;
    }
    #find-row.-replaced {
        height: 6;
    }
    #find-input, #replace-input {
        width: 1fr;
        height: 3;
        border: round #30363d;
        background: #0d1117;
    }
    #find-input:focus, #replace-input:focus {
        border: round #58a6ff;
    }
    #replace-input {
        margin-top: 0;
    }
    #find-count {
        width: auto;
        height: 3;
        color: #6e7681;
        padding: 1 1;
    }
    #find-count.-error {
        color: #f85149;
    }
    #find-toggles, #replace-buttons {
        width: auto;
        height: 3;
        align-horizontal: right;
    }
    #replace-row {
        height: 3;
    }
    """

    class Closed(Message):
        pass

    class StateChanged(Message):
        """Search text or options changed - the app re-queries and paints."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ta = None
        self._trafienia: List[Match] = []
        self._indeks = -1
        self._opcje = Options()
        self._zamien = False
        self._ostatni_zapisany = 0

    # --- skladanie -----------------------------------------------------

    def attach(self, text_area) -> None:
        self._ta = text_area

    def compose(self) -> ComposeResult:
        with Horizontal(id="find-row"):
            yield Input(placeholder="Find", id="find-input")
            yield Static("", id="find-count")
            with Horizontal(id="find-toggles"):
                yield Toggle("Aa", "case", "Match case (Alt+C)")
                yield Toggle("ab", "word", "Whole word (Alt+W)")
                yield Toggle(".*", "regex", "Regex (Alt+R)")
                yield BarButton(IK_GORA, "prev", "Previous (Shift+Enter)")
                yield BarButton(IK_DOL, "next", "Next (Enter)")
                yield BarButton(IK_ZAMKNIJ, "close", "Close (Esc)")
        with Horizontal(id="replace-row"):
            yield Input(placeholder="Replace", id="replace-input")
            with Horizontal(id="replace-buttons"):
                yield BarButton("Replace", "replace", "Replace (Ctrl+Alt+Enter)")
                yield BarButton("Replace All", "replace_all",
                                "Replace all (Ctrl+Alt+Shift+Enter)")

    def on_mount(self) -> None:
        self.add_class("hidden")
        self._sync_toggles()

    # --- otwieranie / zamykanie ---------------------------------------

    def open_find(self) -> None:
        self._set_replace(False)
        self._pokaz()

    def open_replace(self) -> None:
        self._set_replace(True)
        self._pokaz()
        self._fokus_zamiany()

    def _set_replace(self, on: bool) -> None:
        self._zamien = on
        self.query_one("#find-row").set_class(on, "-replaced")
        pole = self.query_one("#replace-input", Input)
        pole.display = on
        self.query_one("#replace-row").display = on

    def _pokaz(self) -> None:
        self.remove_class("hidden")
        pole = self.query_one("#find-input", Input)
        if not pole.value:
            # Ctrl+F z zaznaczonym slowem wstepuje je, tak jak wszyscy
            zazn = self._zaznaczony_tekst()
            if zazn and "\n" not in zazn:
                pole.value = zazn
        pole.focus()
        pole.cursor_position = len(pole.value)
        self._przelicz()

    def _fokus_zamiany(self) -> None:
        """Po otwarciu Ctrl+H fokus ma byc w polu ZAMIEN, nie w FIND.

        Inaczej uzytkownik od razu nadpisuje to, czego szuka.
        """
        self.query_one("#replace-input", Input).focus()

    def close(self) -> None:
        self.add_class("hidden")
        self._trafienia = []
        self._indeks = -1
        if self._ta is not None:
            self._ta.clear_matches()
        if self._ta is not None and not self._ta.has_class("hidden"):
            self._ta.focus()

    def _zaznaczony_tekst(self) -> str:
        if self._ta is None:
            return ""
        z = self._ta.selection
        if z is None or z.start == z.end:
            return ""
        try:
            return self._ta.get_text_range(z.start, z.end)
        except Exception:
            return ""

    # --- stan wyszukiwania --------------------------------------------

    def _sync_toggles(self) -> None:
        for t in self.query(Toggle):
            t.set_on({
                "case": self._opcje.case_sensitive,
                "word": self._opcje.whole_word,
                "regex": self._opcje.regex,
            }[t.klucz])

    def _opcje_z(self, **zmiany) -> None:
        dane = {
            "case_sensitive": self._opcje.case_sensitive,
            "whole_word": self._opcje.whole_word,
            "regex": self._opcje.regex,
        }
        dane.update(zmiany)
        self._opcje = Options(**dane)
        self._sync_toggles()
        self._przelicz()

    def przelacz(self, nazwa: str) -> None:
        if nazwa == "case":
            self._opcje_z(case_sensitive=not self._opcje.case_sensitive)
        elif nazwa == "word":
            self._opcje_z(whole_word=not self._opcje.whole_word)
        elif nazwa == "regex":
            self._opcje_z(regex=not self._opcje.regex)

    @property
    def query_text(self) -> str:
        return self.query_one("#find-input", Input).value

    def wstaw_tekst(self, tekst: str) -> None:
        """Open the bar and seed the query with `tekst`.

        A multi-line selection is NOT a usable search term - `Input` is
        one line and a newline would either be rejected or silently
        truncated, leaving the field showing something the user never
        selected. So only single-line selections are used.
        """
        if not tekst or "\n" in tekst:
            self._pokaz()
            return
        self._pokaz()
        self.query_one("#find-input", Input).value = tekst

    def _przelicz(self, z_kursora: bool = False) -> None:
        tekst = self._ta.text if self._ta is not None else ""
        igla = self.query_text
        licznik = self.query_one("#find-count", Static)
        if not igla:
            self._trafienia = []
            self._indeks = -1
            licznik.update("")
            licznik.remove_class("-error")
            if self._ta is not None:
                self._ta.clear_matches()
            self._aktualizuj_przyciski()
            return
        try:
            trafienia = find_in_text(tekst, igla, self._opcje)
        except SearchError as exc:
            licznik.update("regex error")
            licznik.add_class("-error")
            self._trafienia = []
            self._indeks = -1
            if self._ta is not None:
                self._ta.clear_matches()
            self._aktualizuj_przyciski()
            return
        licznik.remove_class("-error")
        self._trafienia = trafienia
        if not trafienia:
            self._indeks = -1
            licznik.update("no results")
            if self._ta is not None:
                self._ta.clear_matches()
        else:
            start = 0
            if z_kursora and self._ta is not None:
                kursor = self._ta.cursor_location
                for i, m in enumerate(trafienia):
                    if (m.row, m.col) > (kursor[0], kursor[1]):
                        start = i
                        break
                else:
                    start = 0
            self._indeks = start
            licznik.update(Text.from_markup(
                f"[#8b949e]{self._indeks + 1}[/] [#6e7681]of[/] "
                f"[#c9d1d9]{len(trafienia)}[/]"))
            self._pokaz_biezace()
        self._aktualizuj_przyciski()
        self.post_message(self.StateChanged())

    def _aktualizuj_przyciski(self) -> None:
        jest = bool(self._trafienia)
        for nazwa in ("prev", "next", "replace", "replace_all"):
            for b in self.query(BarButton):
                if b.klucz == nazwa:
                    b.set_enabled(jest)

    def _pokaz_biezace(self) -> None:
        if self._ta is None or not (0 <= self._indeks < len(self._trafienia)):
            if self._ta is not None:
                self._ta.clear_matches()
            return
        m = self._trafienia[self._indeks]
        self._ta.set_matches(self._trafienia, biezace=(m.row, m.col))
        self._ustaw_zaznaczenie(m)

    def _ustaw_zaznaczenie(self, m: Match) -> None:
        self._ta.selection = Selection(
            (m.row, m.col), (m.row, m.col + m.length))

    # --- nawigacja -----------------------------------------------------

    def _nastepny(self, kierunek: int) -> None:
        if not self._trafienia or self._ta is None:
            return
        if kierunek > 0:
            self._indeks = (self._indeks + 1) % len(self._trafienia)
        else:
            self._indeks = (self._indeks - 1) % len(self._trafienia)
        m = self._trafienia[self._indeks]
        self._ta.set_matches(self._trafienia, biezace=(m.row, m.col))
        self._ustaw_zaznaczenie(m)
        self._tylko_zmien_licznik()

    def _tylko_zmien_licznik(self) -> None:
        self.query_one("#find-count", Static).update(Text.from_markup(
            f"[#8b949e]{self._indeks + 1}[/] [#6e7681]of[/] "
            f"[#c9d1d9]{len(self._trafienia)}[/]"))

    # --- zamiana --------------------------------------------------------

    def _zamien_jedno(self) -> None:
        if self._ta is None or not self._trafienia:
            return
        m = self._trafienia[self._indeks]
        podmiana = self.query_one("#replace-input", Input).value
        tekst = self._ta.text
        linie = tekst.split("\n")
        if m.row >= len(linie):
            return
        linia = linie[m.row]
        linie[m.row] = (linia[:m.col] + podmiana
                        + linia[m.col + m.length:])
        self._ta.text = "\n".join(linie)
        self._przelicz()
        self._nastepny(1)

    def _zamien_wszystkie(self) -> None:
        if self._ta is None or not self._trafienia:
            return
        podmiana = self.query_one("#replace-input", Input).value
        nowy, ile = replace_all(self._ta.text, self.query_text,
                                self._opcje, podmiana)
        self._ta.text = nowy
        self._przelicz()

    # --- zdarzenia ------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if event.input.id in ("find-input", "replace-input"):
            self._przelicz()
            # Zapytanie ma byc gotowe w panelu SEARCH od razu po
            # otwarciu (Ctrl+Shift+F), wiec aktualizujemy go przy kazdym
            # nacisniu klawisza, a nie dopiero przy otwarciu zakladki.
            self.post_message(self.QueryChanged(self.query_text))

    class QueryChanged(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "find-input":
            self._nastepny(1)
        else:
            self._zamien_jedno()

    def on_toggle_clicked(self, event: Toggle.Clicked) -> None:
        event.stop()
        self.przelacz(event.name)

    def on_bar_button_clicked(self, event: BarButton.Clicked) -> None:
        event.stop()
        # `Clicked` przenosi pole `name` (tak jak `PanelTab.Clicked`
        # w `panel.py`), wiec tu czytamy `event.name`, a nie `event.klucz`.
        if event.name == "next":
            self._nastepny(1)
        elif event.name == "prev":
            self._nastepny(-1)
        elif event.name == "close":
            self.close()
            self.post_message(self.Closed())
        elif event.name == "replace":
            self._zamien_jedno()
        elif event.name == "replace_all":
            self._zamien_wszystkie()

    # --- klawiatura -------------------------------------------------------

    BINDINGS = [
        # `Input` nie wiąże `escape`, ale pasek musi go zgłosić SAM -
        # inaczej zdarzenie wypada do `action_handle_escape` aplikacji.
        # (Binding jest potrzebny, zeby pasek w ogóle zglosil `escape`
        # w `Screen.focused == find-input`.)
        ("escape", "close_bar", "Close"),
    ]

    def action_close_bar(self) -> None:
        self.close()
        self.post_message(self.Closed())
