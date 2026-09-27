import os
import time
import tempfile
from typing import Optional, List, Dict, Any
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ...core.checkpoints import CheckpointManager, Checkpoint


def _split_diff_by_file(diff_text: str) -> List[Dict[str, Any]]:
    """Rozbija output `git diff` na osobne wpisy - jeden na plik.

    Zamiast pokazywac kod w modalu, trzymamy tylko metadane per plik
    (nazwa, ile dodanych/usunietych linii, ile hunkow) oraz sam
    fragment diffu, ktory zostanie wypisany do nowego okna terminala
    po kliknieciu.
    """
    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                entries.append(current)
            rest = line[len("diff --git "):]
            # Format: "a/<stara> b/<nowa>" - bierzemy nowa sciezke.
            path = rest.rsplit(" b/", 1)[-1] if " b/" in rest else rest.split(" ")[-1]
            current = {"path": path, "added": 0, "removed": 0, "hunks": 0, "text": [line]}
        elif current is not None:
            current["text"].append(line)
            if line.startswith("@@"):
                current["hunks"] += 1
            elif line.startswith("+") and not line.startswith("+++"):
                current["added"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                current["removed"] += 1
    if current is not None:
        entries.append(current)
    return entries

CHECKPOINTS_CSS = """
CheckpointsModal {
    align: center middle;
    background: rgba(9, 13, 19, 0.85);
}

/* Fullscreen, frameless - shared by the TUI and the editor. */
#checkpoints-dialog {
    width: 100%;
    height: 100%;
    max-width: 100%;
    max-height: 100%;
    background: #0d1117;
    border: none;
    padding: 2 4;
}

#checkpoints-header {
    height: 3;
    dock: top;
    border-bottom: solid #21262d;
    padding-bottom: 0;
    margin-bottom: 1;
}

#checkpoints-title {
    width: 1fr;
    color: #e6edf3;
    text-style: none;
}

#checkpoints-esc-hint {
    width: auto;
    color: #8b949e;
}

#checkpoints-info-hint {
    height: 1;
    color: #8b949e;
    margin-bottom: 1;
}

#checkpoints-create-bar {
    height: 3;
    margin-bottom: 1;
    align: left middle;
}

/* `border: round` - inaczej po kliknieciu w pole ramka byla
   prostokatem (solid) i wygladala jak obrys, nie jak aktywne pole. */
#checkpoint-desc-input {
    width: 1fr;
    height: 3;
    min-height: 3;
    max-height: 3;
    background: #0d1117;
    color: #e6edf3;
    /* !important - the app-level `Input { border: none !important; max-height: 1 }`
       rule in styles.py would otherwise strip the frame and hide the text. */
    border: round #30363d !important;
    padding: 0 1;
    margin-right: 1;
}

/* Pole nie zmienia koloru na bialy po fokusie - dostaje tylko tlo. */
#checkpoint-desc-input:focus {
    background: #161b22;
    border: round #30363d !important;
}

#btn-create-checkpoint {
    width: auto;
    height: 3;
    background: #238636;
    color: #ffffff;
    border: none;
}

#btn-create-checkpoint:hover {
    background: #2ea043;
}

#checkpoints-scroll {
    width: 100%;
    height: 1fr;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

/* Kafelek aktywnego checkpointu. Wczesniej `border: round #58a6ff`
   dawal jaskrawoniebieska ramke - user prosil, zeby pozycje z ramka
   nie byly podswietlone. Ramka jest teraz taka jak u pozostalych
   (szara), a aktywnosc niesie wypelnienie + pogrubienie. */
.checkpoint-card-active {
    width: 100%;
    height: 3;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #21262d;
    border: round #30363d;
    content-align: left middle;
    text-align: left;
    color: #e6edf3;
    text-style: bold;
}

.checkpoint-card-history {
    width: 100%;
    height: 3;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #161b22;
    border: round #30363d;
    content-align: left middle;
    text-align: left;
    color: #c9d1d9;
}

.checkpoint-card-history:hover {
    background: #21262d;
    border: round #30363d;
    color: #ffffff;
}

#diff-view-pane {
    width: 100%;
    height: 1fr;
    display: none;
}

/* Pasek akcji diffa. Wczesniej byl `dock: bottom` w kolorowym
   pojemniku - wlasnie tego "tabelka na dole" ktorej user nie chcial.
   Teraz: jedna linia, bez tla, bez ramki, u GORY pod naglowkiem. */
#diff-view-toolbar {
    height: 1;
    background: transparent;
    padding: 0;
    margin: 0 0 1 0;
}

#diff-back-btn,
#diff-revert-btn {
    width: auto;
    height: 1;
    background: transparent;
    border: none;
    padding: 0 1;
    color: #58a6ff;
}

#diff-revert-btn {
    color: #f85149;
}

#diff-back-btn:hover,
#diff-revert-btn:hover {
    background: #21262d;
    color: #ffffff;
}

/* Kafelek pojedynczego pliku - zaokraglona tabliczka, klikniecie
   otwiera diff tego pliku w nowym oknie terminala. */
.diff-file-card {
    width: 100%;
    height: 3;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #161b22;
    border: round #30363d;
    content-align: left middle;
    /* `Button` ma domyslne `text-align: center`, czego `content-align`
       nie override'uje - bez tego nazwa pliku byla wycentrowana
       w kafelku zamiast przy lewej krawedzi. */
    text-align: left;
    color: #c9d1d9;
}

.diff-file-card:hover {
    background: #21262d;
    border: round #30363d;
    color: #ffffff;
}

/* Naglowek widoku plikow: ktory checkpoint, opis, ile plikow. */
#diff-view-head {
    width: 100%;
    height: auto;
    padding: 0 1;
    margin-bottom: 1;
    color: #8b949e;
}

#diff-view-scroll {
    width: 100%;
    height: 1fr;
    background: transparent;
    scrollbar-size-vertical: 1;
    scrollbar-gutter: stable;
    padding: 0;
}
"""

class CheckpointsModal(ModalScreen[None]):

    CSS = CHECKPOINTS_CSS

    BINDINGS = [
        Binding("escape", "handle_escape", "Back/Close"),
    ]

    def __init__(self, workdir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.workdir = os.path.abspath(workdir)
        self.manager = CheckpointManager(self.workdir)
        self.showing_diff: bool = False
        self.selected_checkpoint: Optional[Checkpoint] = None
        self._diff_entries: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="checkpoints-dialog"):
            with Horizontal(id="checkpoints-header"):
                yield Static(Text.from_markup("[#58a6ff]CHECKPOINTS[/] [dim]· Change history and checkpoints[/]"), id="checkpoints-title")
                yield Static("[dim]esc[/dim]", id="checkpoints-esc-hint")

            yield Static(Text.from_markup(
                "[dim #8b949e]Checkpoints are saved automatically when the model finishes a turn"
                " — or, in Code mode, when it finishes and you accept the changes.[/]"
            ), id="checkpoints-info-hint")

            with Horizontal(id="checkpoints-create-bar"):
                yield Input(placeholder="Checkpoint name / state description...", id="checkpoint-desc-input")
                yield Button("Create checkpoint", id="btn-create-checkpoint")

            with VerticalScroll(id="checkpoints-scroll"):
                cps = self.manager.list_checkpoints()
                if cps:
                    cps = sorted(cps, key=lambda c: c.timestamp, reverse=True)
                    for c in cps:
                        cls = "checkpoint-card-active" if c.is_active else "checkpoint-card-history"
                        badge = "[bold white]● ACTIVE[/]" if c.is_active else "[dim #8b949e]○ HISTORY[/]"
                        time_s = ""
                        try:
                            time_s = time.strftime("%H:%M:%S", time.localtime(c.timestamp))
                        except Exception:
                            pass
                        label_markup = (
                            f"{badge}  [#58a6ff]{time_s}[/]  [dim]|[/]  "
                            f"[#e6edf3]{c.prompt[:32]}[/]  "
                            f"[#3fb950]+{c.stats.get('added', 0)}[/] "
                            f"[#f85149]−{c.stats.get('removed', 0)}[/]  "
                            f"[dim]({len(c.files_changed)} files)[/]"
                        )
                        yield Button(Text.from_markup(label_markup), classes=cls, name=c.id)
                else:
                    yield Button(Text.from_markup("[dim]○ CHECKPOINT · Baseline repository state[/]"), classes="checkpoint-card-history", name="baseline")

            with Vertical(id="diff-view-pane"):
                # Akcje U GORY, jedna linia, bez ramek. Wczesniej byly
                # na doku (`dock: bottom`) w pojemniku z tlem - user
                # nazwal to "tabelka na dole" i kazal przeniesc.
                with Horizontal(id="diff-view-toolbar"):
                    yield Button("← back", id="diff-back-btn")
                    yield Button("Restore this checkpoint", id="diff-revert-btn")
                yield Static("", id="diff-view-head")
                with VerticalScroll(id="diff-view-scroll"):
                    pass

    async def _refresh_list(self) -> None:
        scroll = self.query_one("#checkpoints-scroll", VerticalScroll)
        await scroll.remove_children()
        cps = self.manager.list_checkpoints()
        if cps:
            cps = sorted(cps, key=lambda c: c.timestamp, reverse=True)
            for c in cps:
                cls = "checkpoint-card-active" if c.is_active else "checkpoint-card-history"
                badge = "[bold white]● ACTIVE[/]" if c.is_active else "[dim #8b949e]○ HISTORY[/]"
                time_s = ""
                try:
                    time_s = time.strftime("%H:%M:%S", time.localtime(c.timestamp))
                except Exception:
                    pass
                label_markup = (
                    f"{badge}  [#58a6ff]{time_s}[/]  [dim]|[/]  "
                    f"[#e6edf3]{c.prompt[:32]}[/]  "
                    f"[#3fb950]+{c.stats.get('added', 0)}[/] "
                    f"[#f85149]−{c.stats.get('removed', 0)}[/]  "
                    f"[dim]({len(c.files_changed)} files)[/]"
                )
                await scroll.mount(Button(Text.from_markup(label_markup), classes=cls, name=c.id))
        else:
            await scroll.mount(Button(Text.from_markup("[dim]○ CHECKPOINT · Baseline repository state[/]"), classes="checkpoint-card-history", name="baseline"))

    @on(Button.Pressed, "#btn-create-checkpoint")
    async def on_create_checkpoint_pressed(self) -> None:
        inp = self.query_one("#checkpoint-desc-input", Input)
        val = inp.value.strip() or "Manual checkpoint"
        cp = self.manager.create_checkpoint(val)
        inp.value = ""
        await self._refresh_list()
        self.notify(f"Checkpoint created: {cp.id}")

    @on(Input.Submitted, "#checkpoint-desc-input")
    async def on_input_submitted(self) -> None:
        await self.on_create_checkpoint_pressed()

    @on(Button.Pressed, "#diff-back-btn")
    def on_back_from_diff(self) -> None:
        self._show_list_view()

    @on(Button.Pressed, "#diff-revert-btn")
    async def on_revert_pressed(self) -> None:
        if not self.selected_checkpoint:
            return
        if self.manager.revert_checkpoint(self.selected_checkpoint.id):
            self.notify(f"Checkpoint restored: {self.selected_checkpoint.id}")
            self._show_list_view()
            await self._refresh_list()
        else:
            self.notify("Failed to restore checkpoint", severity="error")

    @on(Button.Pressed)
    def on_card_pressed(self, event: Button.Pressed) -> None:
        button = event.button
        # Kafelek pliku - diff w nowym oknie terminala.
        if "diff-file-card" in button.classes:
            event.stop()
            path = button.name or ""
            for entry in self._diff_entries:
                if entry["path"] == path:
                    self._open_diff_in_new_terminal(entry)
                    return
            return
        bid = button.id or ""
        if bid in ("diff-back-btn", "diff-revert-btn", "btn-create-checkpoint"):
            return
        cid = button.name
        if cid:
            cps = self.manager.list_checkpoints()
            target = next((c for c in cps if c.id == cid), None)
            if target:
                self._show_diff_view(target)
                return
        dummy_cp = Checkpoint(
            id="baseline",
            prompt="Baseline repository state",
            timestamp=0.0,
            files_changed=[],
            stats={"added": 0, "removed": 0},
            diff="",
            is_active=False
        )
        self._show_diff_view(dummy_cp)

    def _show_diff_view(self, cp: Checkpoint) -> None:
        """Pokazuje LISTE ZMIENIONYCH PLIKOW, nie kod.

        Wczesniej `_show_diff_view` wypisywal pelny diff (tysiace
        kolorowanych linii) - przy jednym dluzym pliku modal zamienial
        sie w sciege tekstu i lista checkpointow przepadala. Teraz
        widok ma tylko pliki w zaokraglonych kafelkach, a klikniecie
        pliku otwiera jego diff w NOWYM OKNIE TERMINALA.
        """
        self.selected_checkpoint = cp
        self.showing_diff = True
        self.query_one("#checkpoints-info-hint").styles.display = "none"
        self.query_one("#checkpoints-create-bar").styles.display = "none"
        self.query_one("#checkpoints-scroll").styles.display = "none"
        self.query_one("#diff-view-pane").styles.display = "block"

        entries = _split_diff_by_file(cp.diff or "")
        if not entries:
            # Brak diffu w metadanych - pokaz nazwy plikow z `files_changed`.
            entries = [
                {"path": p, "added": 0, "removed": 0, "hunks": 0, "text": []}
                for p in (cp.files_changed or [])
            ]

        self._diff_entries = entries

        head = self.query_one("#diff-view-head", Static)
        total_add = sum(e["added"] for e in entries)
        total_del = sum(e["removed"] for e in entries)
        head.update(
            Text.from_markup(
                f"[bold #e6edf3]{cp.prompt or cp.id}[/]  "
                f"[dim]{time.strftime('%H:%M:%S', time.localtime(cp.timestamp))}"
                f"  ·  {len(entries)} files  ·[/]  "
                f"[#3fb950]+{total_add}[/] [dim]|[/] [#f85149]-{total_del}[/]\n"
                f"[dim]click a file to open its diff in a new terminal window[/]"
            )
        )

        scroll = self.query_one("#diff-view-scroll", VerticalScroll)
        scroll.remove_children()
        for entry in entries:
            scroll.mount(
                Button(
                    Text.from_markup(self._file_card_markup(entry)),
                    classes="diff-file-card",
                    name=entry["path"],
                )
            )
        scroll.scroll_home(animate=False)

    @staticmethod
    def _file_card_markup(entry: dict) -> str:
        path = entry["path"]
        # Sciezke scinamy do ostatnich 3 segmentow, zeby miescila sie
        # w kafelku na waskim terminalu.
        parts = path.replace("\\", "/").split("/")
        short = "/".join(parts[-3:]) if len(parts) > 3 else path
        name = Text.from_markup(f"[bold #e6edf3]{short}[/]")
        return (
            f"{name}   "
            f"[#3fb950]+{entry['added']}[/] "
            f"[dim]|[/] "
            f"[#f85149]-{entry['removed']}[/]   "
            f"[dim]{entry['hunks']} hunk(s)[/]"
        )

    def _open_diff_in_new_terminal(self, entry: dict) -> None:
        """Otwiera diff pojedynczego pliku w nowym oknie konsoli.

        Zapisuje diff do pliku `.patch` i odpala `.bat`, ktory robi
        `more < patch` w nowym oknie. Uzywamy `os.startfile`, bo
        `subprocess` z `creationflags=NEW_CONSOLE` dziala w Pythonie
        dopiero od 3.12 - a `wt.exe` na tej maszynie nie ma.
        """
        path = entry["path"]
        stamp = f"{int(time.time())}_{abs(hash(path)) % 100000}"
        base = os.path.join(tempfile.gettempdir(), f"cmdai_diff_{stamp}")
        patch = f"{base}.patch"
        bat = f"{base}.bat"
        try:
            with open(patch, "w", encoding="utf-8", errors="replace") as fh:
                fh.write("\n".join(entry["text"]))
            with open(bat, "w", encoding="utf-8", errors="replace", newline="") as fh:
                fh.write("@echo off\r\n")
                fh.write(f"title CMDAI diff - {path[:60]}\r\n")
                fh.write(f"cd /d \"{self.workdir}\"\r\n")
                fh.write(f"more < \"{patch}\"\r\n")
                fh.write("echo.\r\n")
                fh.write("pause\r\n")
            os.startfile(bat)  # type: ignore[attr-defined]
            self.notify(f"Opened diff in a new terminal window: {path}")
        except Exception as error:
            self.notify(f"Could not open a terminal window: {error}", severity="error")

    def _show_list_view(self) -> None:
        self.showing_diff = False
        self.query_one("#diff-view-pane").styles.display = "none"
        self.query_one("#checkpoints-info-hint").styles.display = "block"
        self.query_one("#checkpoints-create-bar").styles.display = "block"
        self.query_one("#checkpoints-scroll").styles.display = "block"

    def action_handle_escape(self) -> None:
        if self.showing_diff:
            self._show_list_view()
        else:
            self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.action_handle_escape()
