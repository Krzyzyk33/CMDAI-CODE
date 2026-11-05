"""Install the OpenCode-free provider into a running CMDAI CODE process.

Nothing in E:\\CMDAI-CODE is modified. Every change here is made to live
objects at import time, so the app on disk stays byte-identical to its git
checkout and `git status` never shows a thing.

Order matters. `cmdai.tui.app` and `cmdai.tui.modals.models` both bind names out
of `cmdai.core.providers` at module import time:

    app.py:27     from ..core.providers import PROVIDERS_CATALOG, stream_chat_completion
    models.py:13  from ...core.providers import PROVIDERS_CATALOG, fetch_remote_models

So `cmdai.core.providers` is patched first, and those two modules are imported
only afterwards, where they pick up the patched versions.
"""

from __future__ import annotations

import builtins
import os
import sys
from typing import Any, Dict, List

PROVIDER_ID = "opencode_local"


def _patch_providers(app_root: str) -> None:
    """Add the provider to the catalogue and route chat calls to the bridge."""
    from cmdai.core import providers

    if PROVIDER_ID in providers.PROVIDERS_CATALOG:
        return

    info_cls = providers.ProviderInfo
    providers.PROVIDERS_CATALOG[PROVIDER_ID] = info_cls(
        id=PROVIDER_ID,
        name="OpenCode Free (local server)",
        base_url="local://opencode",
        key_url="",
        provider_type="local",
        default_models=["big-pickle", "space-bunny-free", "mimo-v2.6-flash-free"],
        supports_remote_fetch=False,
    )

    # Put it first so it is what a user sees when they open the picker.
    reordered: Dict[str, Any] = {PROVIDER_ID: providers.PROVIDERS_CATALOG[PROVIDER_ID]}
    for key, value in providers.PROVIDERS_CATALOG.items():
        if key != PROVIDER_ID:
            reordered[key] = value
    providers.PROVIDERS_CATALOG.clear()
    providers.PROVIDERS_CATALOG.update(reordered)

    from . import provider as plugin_provider

    original_stream = providers.stream_chat_completion
    original_fetch = providers.fetch_remote_models

    def stream_chat_completion(
        provider_id,
        model_id,
        messages,
        api_key="",
        generation_params=None,
        on_token=None,
        on_thinking=None,
        is_aborted=None,
        on_tool=None,
        on_usage=None,
        *args,
        **kwargs,
    ):
        if provider_id in (PROVIDER_ID, "opencode", "opencode_zen", "local_opencode"):
            return plugin_provider.stream_completion(
                model_id,
                messages,
                generation_params=generation_params,
                on_token=on_token,
                on_thinking=on_thinking,
                is_aborted=is_aborted,
                on_tool=on_tool,
                on_usage=on_usage,
                *args,
                **kwargs,
            )
        return original_stream(
            provider_id,
            model_id,
            messages,
            api_key=api_key,
            generation_params=generation_params,
            on_token=on_token,
            on_thinking=on_thinking,
            is_aborted=is_aborted,
            on_tool=on_tool,
            on_usage=on_usage,
            *args,
            **kwargs,
        )

    def fetch_remote_models(pid, api_key="", *args, **kwargs):
        if pid == PROVIDER_ID:
            return plugin_provider.fetch_models(
                kwargs.get("workdir") or (args[0] if args else "")
            )
        try:
            return original_fetch(pid, api_key, *args, **kwargs)
        except TypeError:
            # The shipped signature takes no workdir.
            return original_fetch(pid, api_key)

    providers.stream_chat_completion = stream_chat_completion
    providers.fetch_remote_models = fetch_remote_models


def _patch_model_modal() -> None:
    """Make the picker fetch our models without an API key.

    `refresh_all_models` only queries providers that have a key or are on a
    hardcoded no-key list, so the new provider would never be fetched.
    """
    from cmdai.tui.modals import models as models_modal

    # models.py bound `fetch_remote_models` at import time.
    models_modal.fetch_remote_models = sys.modules[
        "cmdai.core.providers"
    ].fetch_remote_models

    # Replaces rather than wraps: the original only queries providers that have a
    # key or are on its hardcoded no-key list, so ours would never be fetched.
    def refresh_all_models(self) -> None:
        from cmdai.core.providers import PROVIDERS_CATALOG
        from cmdai.core.settings import get_settings

        settings = get_settings()
        for pid in PROVIDERS_CATALOG:
            if pid == PROVIDER_ID:
                self.fetch_provider_models(pid, "")
            else:
                key = settings.get_api_key(pid)
                if key or pid in ("openrouter", "local_gguf", "ollama", "lmstudio", "vllm"):
                    self.fetch_provider_models(pid, key)

    models_modal.ModelSelectModal.refresh_all_models = refresh_all_models

    # The picker sorts with a hardcoded key that pins local_gguf first:
    #   sorted(PROVIDERS_CATALOG.items(), key=lambda x: 0 if x[0] == "local_gguf" else 1)
    # There is no attribute to influence, so `sorted` is shadowed in the module
    # namespace for the duration of the call. The guard keeps the module's other
    # sorted() (over model dicts, not provider tuples) untouched.
    original_sorted = builtins.sorted

    def patched_sorted(iterable, *args, **kwargs):
        result = original_sorted(iterable, *args, **kwargs)
        try:
            items = list(result)
        except Exception:
            return result
        if not items:
            return result
        head = items[0]
        if (
            isinstance(head, tuple)
            and len(head) == 2
            and isinstance(head[0], str)
            and head[0] in sys.modules["cmdai.core.providers"].PROVIDERS_CATALOG
        ):
            return original_sorted(
                items, key=lambda kv: 0 if kv[0] == PROVIDER_ID else 1
            )
        return result

    models_modal.sorted = patched_sorted


def _patch_app() -> None:
    """Bind the bridge to the project and stop the server on exit."""
    from cmdai.tui import app as app_modal

    app_modal.stream_chat_completion = sys.modules[
        "cmdai.core.providers"
    ].stream_chat_completion

    from .bridge import get_bridge

    original_init = app_modal.CMDAICodeTUI.__init__

    def __init__(self, workdir=".", **kwargs):
        original_init(self, workdir, **kwargs)
        _APP_REF["app"] = self
        try:
            get_bridge(self.workdir)
        except Exception:
            pass

    app_modal.CMDAICodeTUI.__init__ = __init__

    # The app computes `_show_thinking` from the capability cache during
    # startup, which is before any model has answered. Publishing the server's
    # capabilities now means the indicator is right from the first message
    # instead of appearing only after a turn.
    original_refresh = app_modal.CMDAICodeTUI._refresh_capabilities

    def _refresh_capabilities(self) -> None:
        original_refresh(self)
        try:
            from . import provider as plugin_provider
            from .bridge import get_bridge

            models = get_bridge(getattr(self, "workdir", "")).list_models()
            if plugin_provider.publish_capabilities(models):
                original_refresh(self)
                self.call_from_thread(self._update_meta_bars)
        except Exception:
            pass

    app_modal.CMDAICodeTUI._refresh_capabilities = _refresh_capabilities

    original_quit = app_modal.CMDAICodeTUI.action_quit_app

    def action_quit_app(self) -> None:
        try:
            from .bridge import stop_bridge
            stop_bridge()
        except Exception:
            pass
        original_quit(self)

    app_modal.CMDAICodeTUI.action_quit_app = action_quit_app

    original_status = app_modal.CMDAICodeTUI._build_status_body

    def _build_status_body(self) -> str:
        body = original_status(self)
        if getattr(self, "current_provider", "") != PROVIDER_ID:
            return body
        try:
            from .bridge import bridge_status
            st = bridge_status()
        except Exception:
            return body
        if not st.get("started"):
            extra = ("OpenCode:     not started yet (starts on first message)\n"
                     "Free models:  see /models")
        elif st.get("alive"):
            extra = (f"OpenCode:     UP - {st.get('url', '')} - "
                     f"{'started by CMDAI' if st.get('started') else 'attached'}\n"
                     f"Free models:  {st.get('models', 0)} in catalogue")
        else:
            extra = ("OpenCode:     DOWN - "
                     f"{st.get('error', 'not responding')}\n"
                     "Hint:         restart the app")
        return f"{body}\n[dim]{extra}[/]"

    app_modal.CMDAICodeTUI._build_status_body = _build_status_body


_APP_REF: Dict[str, Any] = {}


def refresh_thinking_ui() -> bool:
    """Re-run detection and repaint the input/hero bars.

    Called when a model turns out to reason after all. The label next to the
    model name is built from the detected capability, which is only recomputed
    in `_refresh_capabilities`, and that ran before the first answer arrived -
    so without this the level stays absent for the whole session.
    """
    app = _APP_REF.get("app")
    if app is None:
        return False
    try:
        app._refresh_capabilities()
        app.call_from_thread(app._update_meta_bars)
        return True
    except Exception:
        return False


def install(app_root: str = "") -> List[str]:
    """Patch the app in memory. Safe to call more than once.

    Returns a list of the steps that were applied, for the launcher's output.
    """
    if app_root:
        src = os.path.join(app_root, "src")
        if os.path.isdir(src) and src not in sys.path:
            sys.path.insert(0, src)

    applied: List[str] = []

    _patch_providers(app_root)
    applied.append("provider opencode_local registered (first in catalogue)")

    _patch_model_modal()
    applied.append("model picker fetches opencode_local without an API key")

    _patch_app()
    applied.append("bridge bound to the project; server stopped on exit")

    return applied


def describe() -> str:
    """Short banner for the launcher, so it is obvious the plugin is active."""
    try:
        from .bridge import KNOWN_FREE_MODELS

        return (
            "CMDAI CODE DEV - OpenCode Free provider\n"
            f"  {len(KNOWN_FREE_MODELS)} free models reachable via a local OpenCode server\n"
            "  the checkout in E:\\CMDAI-CODE is not modified\n"
        )
    except Exception:
        return "CMDAI CODE DEV - OpenCode Free provider\n"