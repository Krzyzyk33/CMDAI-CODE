"""Obszar kodu: `TextArea` + tlo diagnostyk + tlo trafien wyszukiwania.

Dwie rzeczy dzieja sie w `render_line`: tlo wiersza z bledem i tlo
trafienia. Obie licza pozycje w **znakach** (tak jak `TextArea.selection`
i jak `core/search.py`), a `Strip` trzyma segmenty w **komorkach**.
Roznica pojawia sie przy emoji i CJK - emoji to 1 znak, ale 2 komorki,
wiec kolumna trafienia musi byc przeliczona, inaczej tlo laduje o
jedna komorke w bok.
"""
from typing import Dict, List, Optional, Tuple

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widgets import TextArea

from .problems import ERROR_BG, WARNING_BG

MATCH_BG = "#1f3b57"
MATCH_CURRENT_BG = "#2b5c8a"


def _podziel_w_komorce(segs, poz: int):
    """Split segments at absolute cell offset `poz` into (before, after).

    `Segment.split_cells` is an instance method that cuts one segment at
    a *cell* offset and handles the 2-cell character correctly, so the
    cut is delegated to it. `Segment.divide` was not used because its
    grouping is not obvious and it silently dropped the tail.
    """
    lewa: List[Segment] = []
    prawa: List[Segment] = []
    przebyte = 0
    for s in segs:
        dl = cell_len(s.text)
        koniec = przebyte + dl
        if koniec <= poz:
            lewa.append(s)
        elif przebyte >= poz:
            prawa.append(s)
        else:
            a, b = s.split_cells(poz - przebyte)
            lewa.append(a)
            prawa.append(b)
        przebyte = koniec
    return lewa, prawa


def _tlo_na_komorkach(strip: Strip, od: int, do: int, tlo) -> Strip:
    """Paint cells [od, do) of the strip with `tlo`."""
    if do <= od or od < 0 or od >= strip.cell_length:
        return strip
    do = min(do, strip.cell_length)
    przed, reszta = _podziel_w_komorce(list(strip), od)
    srodek, po = _podziel_w_komorce(reszta, do - od)
    styl = Style(bgcolor=tlo)
    wypelnione = [Segment(s.text, (s.style or Style()) + styl) for s in srodek]
    return Strip(przed + wypelnione + po, strip.cell_length)


class DiagnosticTextArea(TextArea):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._markers: Dict[int, str] = {}
        self._matches: Dict[int, List[Tuple[int, int]]] = {}
        self._biezace: Optional[Tuple[int, int]] = None
        self._linie: List[str] = []

    @property
    def markers(self) -> Dict[int, str]:
        return dict(self._markers)

    def set_markers(self, markers: Dict[int, str]) -> None:

        if markers == self._markers:
            return
        self._markers = dict(markers)

        self.refresh()

    def clear_markers(self) -> None:
        self.set_markers({})

    def set_matches(self, trafienia, biezace=None) -> None:
        """Paint every search hit; `biezace` is (row, col) of the hit the
        cursor sits on and gets a stronger colour.

        The line list is cached here rather than split per rendered
        line - `render_line` runs for every visible row on every
        repaint, and splitting the whole document each time would make
        scrolling quadratic.
        """
        nowe: Dict[int, List[Tuple[int, int]]] = {}
        for m in trafienia or ():
            nowe.setdefault(m.row, []).append((m.col, m.length))
        self._biezace = tuple(biezace) if biezace else None
        if nowe == self._matches and self._biezace == biezace:
            return
        self._matches = nowe
        self._linie = self.text.split("\n")
        self.refresh()

    def clear_matches(self) -> None:
        self.set_matches(())

    def _komorka(self, row: int, kolumna: int) -> Optional[int]:
        """Character column -> cell column within the line.

        Returns `None` when the line is unknown, and the caller skips
        painting. Falling back to the character column would paint one
        cell off for every line containing an emoji or CJK, which is
        worse than not painting at all.
        """
        if row < 0 or row >= len(self._linie):
            return None
        return cell_len(self._linie[row][:kolumna])

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        row = self.scroll_offset.y + y
        strip = self._narysuj_diagnostyki(strip, row)
        strip = self._narysuj_trafienia(strip, row)
        return strip

    def _przesuniecie_x(self) -> int:
        """How many cells the strip is already shifted left.

        `render_line` returns the whole line INCLUDING the line-number
        gutter (`'1  '`, 3 cells) and starting from `scroll_offset.x`.
        So a match at character column 0 is drawn at
        `gutter_width - scroll_offset.x`, not at 0.

        Measured: with `show_line_numbers=True` the first segments are
        `['1  ', 'd', 'ef foo(): ', ...]` and `gutter_width == 3`.
        Without this offset every highlight lands three cells to the
        left - on top of the gutter.
        """
        return self.gutter_width - self.scroll_offset.x

    def _narysuj_diagnostyki(self, strip: Strip, row: int) -> Strip:
        if not self._markers:
            return strip
        severity = self._markers.get(row)
        if severity is None:
            return strip

        selection = self.selection
        if selection is not None and selection.start != selection.end:

            first, last = sorted((selection.start[0], selection.end[0]))
            if first <= row <= last:
                return strip
        tlo = ERROR_BG if severity == "error" else WARNING_BG
        return _tlo_na_komorkach(strip, 0, strip.cell_length, tlo)

    def _narysuj_trafienia(self, strip: Strip, row: int) -> Strip:
        if not self._matches:
            return strip
        kolumny = self._matches.get(row)
        if not kolumny or row >= len(self._linie):
            return strip
        dlugosc_wiersza = len(self._linie[row])
        przesun = self._przesuniecie_x()
        for kolumna, dlugosc in kolumny:
            # Cache lines come from `set_matches`, so after an edit they
            # can be stale. A hit that no longer fits the line is
            # dropped instead of painted at a made-up offset.
            if kolumna < 0 or kolumna + dlugosc > dlugosc_wiersza:
                continue
            od = self._komorka(row, kolumna)
            do = self._komorka(row, kolumna + dlugosc)
            if od is None or do is None or do <= od:
                continue
            tlo = (MATCH_CURRENT_BG
                   if self._biezace == (row, kolumna) else MATCH_BG)
            strip = _tlo_na_komorkach(strip, od + przesun, do + przesun, tlo)
        return strip
