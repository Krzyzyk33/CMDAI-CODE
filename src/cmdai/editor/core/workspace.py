from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

PROJECTS_DIRNAME = "projects"

_NIEDOZWOLONE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

_REZERWOWANE = {"CON", "PRN", "AUX", "NUL"}
_REZERWOWANE |= {"COM%d" % i for i in range(1, 10)}
_REZERWOWANE |= {"LPT%d" % i for i in range(1, 10)}

def editor_dir() -> Path:

    return Path(__file__).resolve().parent.parent

def projects_root() -> Path:
    return editor_dir() / PROJECTS_DIRNAME

def sanitize_project_name(raw: str) -> str | None:

    nazwa = (raw or "").strip().strip('"')
    nazwa = _NIEDOZWOLONE.sub("-", nazwa)
    nazwa = nazwa.rstrip(". ")
    nazwa = nazwa.strip()
    if not nazwa or nazwa in (".", ".."):
        return None

    if nazwa.startswith("."):
        return None
    if nazwa.upper() in _REZERWOWANE:
        return None
    if os.sep in nazwa or (os.altsep and os.altsep in nazwa):
        return None
    return nazwa

def list_projects() -> list[Path]:

    root = projects_root()
    if not root.is_dir():
        return []
    out = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    return sorted(out, key=lambda p: p.name.lower())

def create_project(raw_name: str) -> Path | None:

    nazwa = sanitize_project_name(raw_name)
    if nazwa is None:
        return None

    projekt = projects_root() / nazwa
    if projekt.exists():
        return None
    try:
        (projekt / ".cmdai_code_project" / "checkpoints").mkdir(parents=True)
    except OSError:
        return None
    return projekt

def delete_project(path) -> bool:
    """Usuwa katalog projektu wraz z trescia. Zwraca `True` gdy sie
    udalo.

    Bezpieczenstwo jest tu istotne, bo `rmtree` kasuje rekurencyjnie
    wszystko, co uzytkownik w nim trzyma - czyli jego kod. Dlatego
    sprawdzamy trzy rzeczy, zanim cokolwiek usuniemy:

      1. sciezka musi lezec BEZPOSREDNIO w `projects/` - nie glebiej
         i nie w dowolnym miejscu dysku. `path.parent == root` nie
         dopuszcza `projects/nested/x` ani `projects/../src`,
      2. sciezka nie moze byc samym `projects/`,
      3. `resolve()` symlinkow, zeby dzwignie symboliczny wskazujacy
         na katalog poza `projects/` tez nie przeszedl.

    Katalogi ukryte (`.cos`) nie moga byc celem - `list_projects()`
    ich nie pokazuje, wiec nie da sie ich wybrac w UI.
    """
    root = projects_root().resolve()
    if not root.is_dir():
        return False
    try:
        cel = Path(path).resolve()
    except (OSError, ValueError):
        return False

    if cel == root or cel.parent != root:
        return False
    if cel.name.startswith("."):
        return False
    if not cel.is_dir():
        return False

    try:
        shutil.rmtree(cel)
    except OSError:
        return False
    return True

def project_size(path) -> int:
    """Liczba plikow rekurencyjnie (do etykiety w UI). Zwraca -1 gdy
    nie da sie policzyc - wtedy UI nie pokazuje rozmiaru."""
    try:
        return sum(1 for p in Path(path).rglob("*") if p.is_file())
    except OSError:
        return -1
