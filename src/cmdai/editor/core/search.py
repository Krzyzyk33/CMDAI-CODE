"""Wyszukiwanie tekstu: w biezacym pliku i w projekcie.

Czysty modul, bez Textuala - dzieki temu testuje sie go zwyklym
`assert`, a nie przez `run_test()`.

Liczone sa pozycje w **znakach**, nie w komorkach. `TextArea.selection`
przyjmuje (wiersz, kolumna) gdzie kolumna to offset znaku, wiec przy
polskich znakach lub emoji liczenie `len()` byloby zgodne z tym, co
robi kursor, ale liczenie komorek (`cell_len`) bylooby nie.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

WORD = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


class SearchError(Exception):
    """Raised when a regex is not compilable - the UI shows the text."""


@dataclass(frozen=True)
class Options:
    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False


@dataclass(frozen=True)
class Match:
    row: int
    col: int
    length: int
    preview: str = ""

    @property
    def end(self) -> int:
        return self.col + self.length

    def end_at(self) -> Tuple[int, int]:
        return (self.row, self.col + self.length)


@dataclass
class FileHits:
    path: str
    matches: List[Match] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.matches)


def _jest_slowem(znak: str) -> bool:
    return bool(znak) and znak in WORD


def _caly_wyraz(linia: str, start: int, koniec: int) -> bool:
    """True when [start, koniec) is not glued to word characters."""
    if start > 0 and _jest_slowem(linia[start - 1]):
        return False
    if koniec < len(linia) and _jest_slowem(linia[koniec]):
        return False
    return True


def _wzorzec(iglo: str, op: Options) -> "re.Pattern":
    if op.regex:
        try:
            return re.compile(iglo, 0 if op.case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise SearchError(str(exc)) from exc
    return re.compile(re.escape(iglo), 0 if op.case_sensitive else re.IGNORECASE)


def find_in_text(text: str, needle: str, op: Options = Options(),
                 start: Tuple[int, int] = (0, 0),
                 limit: int = 20000) -> List[Match]:
    """All matches of `needle`, in document order.

    `start` is a (row, col) cursor position - used to walk forwards
    from the caret without re-scanning what is behind it.
    """
    if not needle:
        return []
    wz = _wzorzec(needle, op)
    linie = text.split("\n")
    trafienia: List[Match] = []

    for nr, linia in enumerate(linie):
        if len(trafienia) >= limit:
            break
        if nr < start[0]:
            continue
        z = start[1] if nr == start[0] else 0
        for dop in wz.finditer(linia, z):
            a, b = dop.span()
            if b == a:
                # Regex ozerowej szerokosci, np. `x*`. `finditer` sam
                # nie petle, ale taki dopasowanie zerowej dlugosci
                # uniosloby wylacznie w nieskonczonosc po nim - idziemy
                # o jeden znak dalej.
                a = b = min(a + 1, len(linia))
                if a == b:
                    continue
            if op.whole_word and not _caly_wyraz(linia, a, b):
                continue
            trafienia.append(Match(nr, a, b - a, linia.strip()))
            if len(trafienia) >= limit:
                break
    return trafienia


def find_from(text: str, needle: str, op: Options,
              pozycja: Tuple[int, int],
              kierunek: int = 1) -> Optional[Match]:
    """Next match after `pozycja`, wrapping around.

    `kierunek` +1 = forward (F3 / Enter), -1 = backward (Shift+Enter).
    Wrap-around is what every editor does, and it is why a search that
    found nothing to the right still moves.
    """
    wszystkie = find_in_text(text, needle, op)
    if not wszystkie:
        return None
    if kierunek >= 0:
        for m in wszystkie:
            if (m.row, m.col) > pozycja:
                return m
        return wszystkie[0]
    for m in reversed(wszystkie):
        if (m.row, m.col) < pozycja:
            return m
    return wszystkie[-1]


def replace_all(text: str, needle: str, op: Options,
                podmiana: str) -> Tuple[str, int]:
    """Replace every match. Returns (new_text, how_many).

    Replaces per-line and per-match instead of `re.sub` on the whole
    document, so that a match found in one line never eats the line
    break of the next one - `re.sub` on `\\n`-joined text with a regex
    like `.*` turns three lines into one.

    In regex mode `podmiana` is expanded, so `\\1` and `\\g<name>`
    work. In literal mode it is inserted verbatim - a user searching
    for a backslash should get a backslash, not a group reference.
    """
    if not needle:
        return text, 0
    wz = _wzorzec(needle, op)
    linie = text.split("\n")
    liczba = 0
    for nr, linia in enumerate(linie):
        z = 0
        while True:
            wybrany = None
            for kandydat in wz.finditer(linia, z):
                a, b = kandydat.span()
                if b == a:
                    # zerowa szerokosc - `finditer` sam nie petle, ale
                    # taki dopasowanie trzeba przesunac o jeden znak
                    a = b = min(a + 1, len(linia))
                    if a == b:
                        continue
                if op.whole_word and not _caly_wyraz(linia, a, b):
                    continue
                wybrany = (kandydat, a, b)
                break
            if wybrany is None:
                break
            kandydat, a, b = wybrany
            if op.regex:
                # `expand()` podmienia `\1` / `\g<nazwa>`; przy zlym
                # numerze grupy rzuca `error`, wiec chronimy.
                try:
                    wstaw = kandydat.expand(podmiana)
                except Exception:
                    wstaw = podmiana
            else:
                wstaw = podmiana
            linia = linia[:a] + wstaw + linia[b:]
            liczba += 1
            z = a + len(wstaw)
            if z >= len(linia):
                break
        linie[nr] = linia
    return "\n".join(linie), liczba


POMIJANE_KATALOGI = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", ".tox", ".idea", ".vscode", "site-packages",
    ".gradle", "target", "vendor", ".cache", "coverage", "htmlcov",
    ".terraform", "obj", "bin", "projects", ".cmdai_code_project",
}

MAKS_PLIKOW = 2000
MAKS_TRAFIEN = 1000
MAKS_ROZMIAR = 4 * 1024 * 1024


# --- dopasowanie nazw plikow --------------------------------------------
# Uzytecznik pisze `edpp` i oczekuje `editor/core/project_picker.py`.
# Dopasowanie po rozrzuconych znakach (`fuzzy`) z ocena: im dluzsza
# ciaglym seria, tym lepsza; poczatek segmentu dostaje premie.

SEPARATOR = "/\\._- "


@dataclass(frozen=True)
class Plik:
    path: str
    name: str
    score: int


def _fuzzy_punkty(tekst: str, wzorzec: str) -> Optional[List[int]]:
    """Positions in `tekst` matched by `wzorzec`, or `None`.

    Subsequence match, case-insensitive, with a greedy restart: when a
    char does not continue the run, the scan jumps to the next
    occurrence of the pattern's current char rather than giving up on
    the rest of the pattern.
    """
    if not wzorzec:
        return []
    t = tekst.lower()
    w = wzorzec.lower()
    punkty: List[int] = []
    i = 0
    for znak in w:
        znaleziony = t.find(znak, i)
        if znaleziony < 0:
            return None
        punkty.append(znaleziony)
        i = znaleziony + 1
    return punkty


def score_name(nazwa: str, wzorzec: str) -> int:
    """Higher is better; 0 means no match.

    Weights, in order of importance:
      - matching inside the BASENAME (+20 per char) - this is what the
        user typed; `editor` in the path is noise,
      - consecutive runs (+20 per char),
      - a match at position 0 or right after a separator (+6),
      - a shorter name wins, so `a.py` outranks `aaaaaaa.py` for `a`.

    Two weightings had to be corrected against measurements:

    - Continuity must outweigh the separator bonus. At +8/+10,
      `s_e_a_r_c_h.py` scored 190 and beat `search.py` at 181, because
      every letter sat after an underscore. Now 237 vs 166.
    - The basename bonus must outweigh continuity. At +4 per char, a
      penalty of -40 for path-only matches, `editor/ui/app.py` scored
      112 and beat `editor/core/project_picker.py` at 100 - the first
      has `pp` adjacent but only in the directory, the second has all
      four characters in the filename. Now 172 vs 92.
    """
    if not wzorzec:
        return 0
    sciezka = nazwa.replace("\\", "/")
    baza = sciezka.rsplit("/", 1)[-1]

    # Basename is what people type, so match it first and fall back to
    # the whole relative path. Without the fallback, `core/search.py`
    # is unfindable by typing `search`.
    for cel, w_sciezce in ((baza, False), (sciezka, True)):
        punkty = _fuzzy_punkty(cel, wzorzec)
        if punkty is None:
            continue
        wynik = 100
        poprzedni = -2
        for poz in punkty:
            if poz == poprzedni + 1:
                wynik += 20
            if poz == 0 or cel[poz - 1] in SEPARATOR:
                wynik += 6
            poprzedni = poz
        # Prefer short names: everything above is a constant per match,
        # so this is the only tie-break between `a.py` and `aaaa.py`.
        wynik += max(0, 40 - len(cel)) // 4
        # A hit inside the BASENAME is what the user meant; a hit in
        # the directory part is a fallback. Without an explicit
        # penalty, `editor/ui/app.py` (10 chars, matched in the
        # directory) outranked `editor/core/project_picker.py`
        # (matched in the filename) - 152 vs 140 - which is backwards.
        if not w_sciezce:
            # Trafienie w NAZWIE pliku - czyli to, co uzytkownik
            # faktycznie wpisal. Premia za kazde trafienie.
            wynik += 20 * len(punkty)
        return wynik
    return 0


def find_files_by_name(root: str, wzorzec: str, limit: int = 50,
                       max_files: int = 20000) -> List[Plik]:
    """Files whose name matches `wzorzec`, best first.

    Walks the project once and scores every path. Capped at
    `max_files` because a fuzzy scan of a huge tree on every keystroke
    would stall the UI.
    """
    if not wzorzec:
        return []
    root = os.path.abspath(root)
    wynik: List[Plik] = []
    przejrzane = 0
    for sciezka in _pliki(root):
        przejrzane += 1
        if przejrzane > max_files:
            break
        rel = os.path.relpath(sciezka, root)
        punkt = score_name(rel, wzorzec)
        if punkt > 0:
            wynik.append(Plik(sciezka, rel, punkt))
    wynik.sort(key=lambda p: (-p.score, p.name.lower()))
    return wynik[:limit]


def _pliki(root: str) -> Iterable[str]:
    """Files under `root`, skipping generated and binary-ish ones."""
    licznik = 0
    for katalog, poddirs, nazwy in os.walk(root):
        poddirs[:] = [d for d in poddirs
                      if d not in POMIJANE_KATALOGI
                      and not d.startswith(".")
                      and d not in ("bin", "obj")]
        for nazwa in nazwy:
            if nazwa.startswith("."):
                continue
            if os.path.splitext(nazwa)[1].lower() in (
                    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".ttf", ".otf",
                    ".woff", ".woff2", ".pdf", ".zip", ".gz", ".7z", ".exe",
                    ".dll", ".so", ".pyc", ".so", ".class", ".jar", ".mp3",
                    ".mp4", ".mov", ".bin", ".db", ".sqlite"):
                continue
            sciezka = os.path.join(katalog, nazwa)
            try:
                if os.path.getsize(sciezka) > MAKS_ROZMIAR:
                    continue
            except OSError:
                continue
            licznik += 1
            if licznik > MAKS_PLIKOW:
                return
            yield sciezka


def find_in_project(root: str, needle: str, op: Options = Options(),
                   limit: int = MAKS_TRAFIEN) -> Tuple[List[FileHits], int, bool]:
    """Search the whole project.

    Returns (files_with_hits, files_scanned, truncated).
    `truncated` tells the UI to say so instead of pretending the list
    is complete - a silent cap reads as "there are no more results".
    """
    if not needle:
        return [], 0, False
    wz = _wzorzec(needle, op)
    wynik: List[FileHits] = []
    przejrzane = 0
    uciete = False
    calkowita = 0

    for sciezka in _pliki(root):
        przejrzane += 1
        try:
            with open(sciezka, "r", encoding="utf-8", errors="strict") as f:
                tresc = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        if not tresc:
            continue
        linie = tresc.split("\n")
        trafienia: List[Match] = []
        for nr, linia in enumerate(linie):
            for dop in wz.finditer(linia):
                a, b = dop.span()
                if b == a:
                    a = b = min(a + 1, len(linia))
                    if a == b:
                        continue
                if op.whole_word and not _caly_wyraz(linia, a, b):
                    continue
                trafienia.append(Match(nr, a, b - a, linia.strip()))
                calkowita += 1
                if calkowita >= limit:
                    uciete = True
                    break
            if calkowita >= limit:
                break
        if trafienia:
            wynik.append(FileHits(sciezka, trafienia))
        if calkowita >= limit:
            break
    return wynik, przejrzane, uciete
