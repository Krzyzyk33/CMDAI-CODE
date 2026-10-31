"""Panel SEARCH - wyniki wyszukiwania w calej projekcie.

Osobny widget, a nie kolejna zakladka `ProblemsPane`, bo tam wyniki
sa z diagnostyki (blad/w ostrzezenie) i maja inne kolory. Tutaj trafienie
jest neutralne.

Wyniki sa plikami z trafieniami; klik w plik rozwija, klik w trafienie
otwiera plik i stawia kursor. Skanowanie leci w watku, bo
`find_in_project` czyta pliki i blokuje UI na czas rzedu setek
milisekund - przy wiekszym projekcie to widoczne szarpniecie.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional

from rich.text import Text
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..core.search import FileHits, Options, SearchError, find_in_project
from .clickable import MouseActivatable

CHEVRON_CLOSED = "\u25f8"
CHEVRON_OPEN = "\u25be"
FILE_FG = "#58a6ff"
MATCH_FG = "#d29922"
DIM_FG = "#6e7681"
HIT_BG = "#3a2d0d"


class SearchFileHeader(MouseActivatable, Static):
    """One file with its hit count; click toggles the group."""

    DEFAULT_CSS = """
    SearchFileHeader {
        width: 100%;
        height: 1;
        padding: 0 1;
        background: transparent;
        color: #c9d1d9;
    }
    SearchFileHeader:hover {
        background: #21262d;
    }
    """

    class Clicked(Message):
        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    def __init__(self, path: str, name: str, count: int,
                 collapsed: bool, **kwargs):
        super().__init__(self._build(name, count, collapsed), **kwargs)
        self.path = path

    @staticmethod
    def _build(name: str, count: int, collapsed: bool) -> Text:
        text = Text()
        text.append(CHEVRON_CLOSED if collapsed else CHEVRON_OPEN,
                    style=DIM_FG)
        text.append(" ")
        text.append(name, style=f"bold {FILE_FG}")
        text.append(f"   {count}", style=DIM_FG)
        return text

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.path))


class SearchHitRow(MouseActivatable, Static):

    DEFAULT_CSS = """
    SearchHitRow {
        width: 100%;
        height: 1;
        padding: 0 1 0 5;
        background: transparent;
        color: #c9d1d9;
    }
    SearchHitRow:hover {
        background: #21262d;
    }
    """

    class Clicked(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    def __init__(self, path: str, row: int, col: int, preview: str,
                 needle: str, **kwargs):
        super().__init__(self._build(row, preview, needle), **kwargs)
        self.path = path
        self.row = row
        self.col = col

    @staticmethod
    def _build(row: int, preview: str, needle: str) -> Text:
        text = Text()
        text.append(f"{row + 1:>4}: ", style=DIM_FG)
        # Highlight the needle inside the preview so the eye lands on it.
        start = preview.find(needle) if needle else -1
        if start < 0 and needle:
            start = preview.lower().find(needle.lower())
        if start < 0:
            text.append(preview, style="#c9d1d9")
            return text
        text.append(preview[:start], style="#c9d1d9")
        text.append(preview[start:start + len(needle)], style=f"bold {MATCH_FG}")
        text.append(preview[start + len(needle):], style="#c9d1d9")
        return text

    def on_activate(self) -> None:
        self.post_message(self.Clicked(self.path, self.row, self.col))


class SearchPane(VerticalScroll):

    DEFAULT_CSS = """
    SearchPane {
        width: 100%;
        height: 100%;
        background: #0d1117;
    }
    """

    class Opened(Message):
        def __init__(self, path: str, row: int, col: int) -> None:
            super().__init__()
            self.path = path
            self.row = row
            self.col = col

    class Scanned(Message):
        """Scan finished - carries the summary line for the tab."""

        def __init__(self, files: int, hits: int, scanned: int,
                     truncated: bool) -> None:
            super().__init__()
            self.files = files
            self.hits = hits
            self.scanned = scanned
            self.truncated = truncated

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root = "."
        self._pliki: List[FileHits] = []
        self._zwiniete = set()
        self._needle = ""
        self._kolejka = None

    def set_root(self, root: str) -> None:
        self._root = os.path.abspath(root)

    def clear(self) -> None:
        self._pliki = []
        self._needle = ""
        self.call_after_refresh(self._wypisz)

    async def search(self, needle: str, op: Options = Options()) -> None:
        """Run the scan without blocking the UI."""
        self._needle = needle
        if not needle:
            self._pliki = []
            self._zwiniete = set()
            await self._wypisz()
            self.post_message(self.Scanned(0, 0, 0, False))
            return
        await self._skanuj(needle, op)

    async def _skanuj(self, needle: str, op: Options) -> None:
        try:
            pliki, przejrzane, uciete = await asyncio.to_thread(
                find_in_project, self._root, needle, op)
        except SearchError as exc:
            self.query_one(Static).update(Text.from_markup(
                f"[#f85149]Bad regex:[/] {exc}"))
            self.post_message(self.Scanned(0, 0, 0, False))
            return
        except asyncio.CancelledError:
            raise
        self._pliki = pliki
        self._zwiniete = set()
        await self._wypisz()
        self.post_message(self.Scanned(
            len(pliki), sum(p.count for p in pliki), przejrzane, uciete))

    def _zbuduj(self):
        """Widgets for the current state, or `None` if nothing changed."""
        if not self._needle:
            return [Static("Type in the search field to search the whole "
                           "project.", id="search-hint")]
        if not self._pliki:
            return [Static(f"No results for '{self._needle}'.",
                           id="search-hint")]
        dzieci = []
        for plik in self._pliki:
            nazwa = os.path.relpath(plik.path, self._root)
            zwiniety = plik.path in self._zwiniete
            dzieci.append(SearchFileHeader(plik.path, nazwa, plik.count,
                                           zwiniety))
            if zwiniety:
                continue
            for m in plik.matches:
                dzieci.append(SearchHitRow(plik.path, m.row, m.col,
                                           m.preview, self._needle))
        return dzieci

    async def _wypisz(self) -> None:
        """Replace the result list with a freshly built one.

        THE one thing that matters here: `remove_children()` returns an
        `AwaitRemove`, and `Widget.mount` raises `MountError` unless
        `self.is_attached`. A bare `remove_children()` followed by
        `mount_all()` therefore fails - the removal has not happened
        yet, so the pane is briefly detached and mounting refuses.

        Measured on a bare `VerticalScroll`:

            children=['a'] is_attached=True
            after bare remove_children()  -> MountError
            after `await remove_children()` -> is_attached=True, mount OK

        So: `await` the removal, then mount. That also makes the
        rebuild reusable any number of times, which `ProblemsPane`
        relies on in `problems.py`.
        """
        dzieci = self._kolejka if self._kolejka is not None else self._zbuduj()
        self._kolejka = None
        if not dzieci:
            return
        await self.remove_children()
        if self.is_attached:
            self.mount_all(dzieci)
        else:
            self._kolejka = dzieci

    def mount_queued(self) -> None:
        """Mount pending results, for a pane that just became visible."""
        if self._kolejka and self.is_attached:
            dzieci, self._kolejka = self._kolejka, None
            self.mount_all(dzieci)

    def on_show(self, event) -> None:
        self.mount_queued()

    def on_search_file_header_clicked(
        self, event: SearchFileHeader.Clicked
    ) -> None:
        event.stop()
        if event.path in self._zwiniete:
            self._zwiniete.discard(event.path)
        else:
            self._zwiniete.add(event.path)
        self.call_after_refresh(self._wypisz)

    def on_search_hit_row_clicked(self, event: SearchHitRow.Clicked) -> None:
        event.stop()
        self.post_message(self.Opened(event.path, event.row, event.col))
