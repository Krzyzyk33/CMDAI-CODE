"""Formatowanie zrodla: `ruff format`, a gdy go nie ma - `black`.

Osobny modul, nie metoda w `BufferManager`, bo `BufferManager` nie
wolno wiedziec o procesach zewnetrznych, a `format` jest wlasnie
tym: uruchamia podproces z plikiem tymczasowym.

Wazne: formatowanie NIGDY nie moze zgubic kodu. Jesli narzedzie
wroci blad albo tekst wyjsciowy jest niekompletny, zwracamy to, co
przyszlo - zawieszony `black` to nie powod, zeby zapisac plik pusty.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

TIMEOUT_SEKUNDY = 20

# `ruff format` przyjmuje `--stdin-filename`, wiec czytamy wynik ze
# `stdout`. Wariant z plikiem tymczasowym bylby prostszy, ale wymaga
# drugiego procesu do odczytu i zapisu.
KOLEJNOSC = ("ruff", "black")

PYTHONOWE = {".py", ".pyi", ".pyw"}

_sciezka: Optional[str] = None
_sprawdzone = False


@dataclass
class FormatResult:
    text: str
    ok: bool
    changed: bool
    error: str = ""
    tool: str = ""


def which_formatter() -> Optional[str]:
    """First available formatter, or `None`.

    The result is cached: this runs on every save, and `shutil.which`
    walks `PATH` each call. `reset_cache()` exists for tests and for
    installing a formatter mid-session.
    """
    global _sciezka, _sprawdzone
    if _sprawdzone:
        return _sciezka
    _sprawdzone = True
    for nazwa in KOLEJNOSC:
        if shutil.which(nazwa):
            _sciezka = nazwa
            return _sciezka
    _sciezka = None
    return None


def reset_cache() -> None:
    global _sciezka, _sprawdzone
    _sciezka = None
    _sprawdzone = False


def formatter_available() -> bool:
    return which_formatter() is not None


def _rozszerzenie(sciezka: str) -> str:
    return os.path.splitext(sciezka or "")[1].lower()


def _uruchom(argv: List[str], kod: str, nazwa: str) -> FormatResult:
    """Run a formatter over stdin, never raising.

    A missing tool, a crash or a timeout all mean "leave the code
    alone" - the editor must stay usable without a formatter.
    """
    try:
        proc = subprocess.run(
            argv, input=kod, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_SEKUNDY)
    except FileNotFoundError:
        return FormatResult(kod, False, False, f"{nazwa}: not found", nazwa)
    except subprocess.TimeoutExpired:
        return FormatResult(kod, False, False, f"{nazwa}: timeout", nazwa)
    except OSError as exc:
        return FormatResult(kod, False, False, f"{nazwa}: {exc}", nazwa)

    if proc.returncode != 0:
        # `black`/`ruff` zapisuja diagnostyke na stderr; stdout jest
        # wtedy pusty, wiec zwracamy oryginal.
        blad = (proc.stderr or "").strip().splitlines()
        opis = blad[0] if blad else f"exit {proc.returncode}"
        return FormatResult(kod, False, False, f"{nazwa}: {opis}", nazwa)

    wyjscie = proc.stdout or ""
    if not wyjscie.strip() and kod.strip():
        # Czysty stdout przy niepustym wejsciu = narzedzie cos
        # zjadlo. Bezpieczniej zostawic kod w spokoju.
        return FormatResult(kod, False, False,
                            f"{nazwa}: empty output", nazwa)
    return FormatResult(wyjscie, True, wyjscie != kod, "", nazwa)


def format_source(sciezka: str, kod: str) -> FormatResult:
    """Format `kod` as the contents of `sciezka`.

    Returns a `FormatResult`; `text` always holds something safe to
    write - the input unchanged when formatting did not happen.
    """
    if not kod or _rozszerzenie(sciezka) not in PYTHONOWE:
        return FormatResult(kod, False, False, "not python", "")
    narz = which_formatter()
    if narz is None:
        return FormatResult(kod, False, False, "no formatter", "")
    if narz == "ruff":
        argv = ["ruff", "format", "--stdin-filename", sciezka, "-"]
    else:
        argv = ["black", "--quiet", "-"]
    return _uruchom(argv, kod, narz)
