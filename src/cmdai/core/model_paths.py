"""Single source of truth for where local GGUF models live.

Before this module, six places each resolved the models directory on
their own, and four of them still carried hardcoded paths under a
``CMDAI CODE`` name with a space in it. Those paths stopped matching
when the checkout moved to ``E:\\CMDAI-CODE`` (hyphen), so the guessing
had been dead weight for a while - it just never showed because a
working candidate happened to come earlier in the list.

Resolution order, first hit wins:

1. an explicit argument, when the caller already knows the directory
2. ``CMDAI_MODELS_DIR`` environment variable
3. ``models_dir`` from config.json
4. ``<app root>/models``

No candidate lists, no per-drive fallbacks. If the resolved directory
has no ``.gguf`` in it, that is reported as "not found" rather than
quietly trying the next drive.

``model_id`` may be a bare filename, a path relative to the models
directory, or an absolute path. Absolute paths are honoured as given
so a caller that already resolved the file does not get second-guessed.
"""

import os
from typing import List, Optional

DEFAULT_MODELS_DIRNAME = "models"
MODELS_DIR_ENV = "CMDAI_MODELS_DIR"


def app_root() -> str:
    """Locate the CMDAI CODE checkout, same way cli.py does.

    Kept independent rather than imported from cli.py: core must not
    depend on the CLI entry point.
    """
    override = os.environ.get("CMDAI_CODE_ROOT")
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    # This file sits at <root>/src/cmdai/core/, so the root is four levels
    # up. cli.py needs three, which is why copying its arithmetic verbatim
    # lands on <root>/src and then fails the marker check below.
    here = os.path.abspath(__file__)
    for _ in range(4):
        here = os.path.dirname(here)
    for marker in ("cmdai.py", "config.example.json", "requirements.txt"):
        if os.path.exists(os.path.join(here, marker)):
            return here
    return here


def _from_config() -> str:
    try:
        from .settings import get_settings
        return str(get_settings().config.get("models_dir") or "")
    except Exception:
        # Settings may not be loadable yet (first run, mid-migration).
        # A missing config is not a reason to fail model discovery.
        return ""


def resolve_models_dir(explicit: str = "") -> str:
    """Return the absolute models directory.

    Never returns a path that does not exist as the result of *guessing*.
    It will return a path that does not exist yet if that is what the
    configuration says, so callers can offer to create it.
    """
    if explicit:
        return os.path.abspath(explicit)

    env_dir = os.environ.get(MODELS_DIR_ENV, "").strip()
    if env_dir:
        return os.path.abspath(os.path.expanduser(env_dir))

    cfg_dir = _from_config().strip()
    if cfg_dir:
        cfg_dir = os.path.expanduser(cfg_dir)
        # A relative configured value is relative to the app root, not to
        # whatever directory a subprocess happened to be launched from.
        if not os.path.isabs(cfg_dir):
            cfg_dir = os.path.join(app_root(), cfg_dir)
        return os.path.abspath(cfg_dir)

    return os.path.join(app_root(), DEFAULT_MODELS_DIRNAME)


def models_dir_with_gguf(explicit: str = "") -> Optional[str]:
    """The models directory, but only if it actually holds a .gguf file.

    Used by the loaders that need to answer "where are my models" rather
    than "what does the config say". Returns None when the directory is
    absent or empty, so the caller can say so instead of reporting a
    stale model list.
    """
    path = resolve_models_dir(explicit)
    if not os.path.isdir(path):
        return None
    try:
        for name in os.listdir(path):
            if name.lower().endswith(".gguf"):
                return path
    except OSError:
        return None
    return None


def list_gguf_files(explicit: str = "") -> List[str]:
    """Absolute paths of the .gguf files in the models directory."""
    path = resolve_models_dir(explicit)
    if not os.path.isdir(path):
        return []
    out: List[str] = []
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith(".gguf"):
            continue
        full = os.path.join(path, name)
        if os.path.isfile(full):
            out.append(full)
    return out


def resolve_model_file(model_id: str, models_dir: str = "", workdir: str = "") -> str:
    """Find a model file by id. Returns "" when not found.

    ``model_id`` may be:
      - an absolute or relative path to an existing file (honoured as given)
      - a bare filename, looked up in the models directory

    ``workdir`` is honoured only for a relative ``model_id`` that already
    carries a directory component, so that ``./models/x.gguf`` from a
    project checkout keeps working. It is not a second models directory.
    """
    if not model_id:
        return ""

    model_id = os.path.expanduser(model_id)

    # An id that already resolves, relative to cwd or absolute.
    if os.path.isabs(model_id) and os.path.isfile(model_id):
        return os.path.abspath(model_id)

    base = os.path.basename(model_id)
    root = resolve_models_dir(models_dir)

    candidates: List[str] = []
    if os.path.dirname(model_id):
        # model_id has a directory part, so it is a path, not a bare name.
        if workdir:
            candidates.append(os.path.join(workdir, model_id))
        candidates.append(os.path.join(root, model_id))
        candidates.append(model_id)
    else:
        candidates.append(os.path.join(root, model_id))
        candidates.append(os.path.join(root, base))

    for cand in candidates:
        if cand and os.path.isfile(cand):
            return os.path.abspath(cand)
    return ""
