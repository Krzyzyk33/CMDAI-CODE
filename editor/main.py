import os
import sys

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

editor_dir = os.path.dirname(os.path.abspath(__file__))
if editor_dir not in sys.path:
    sys.path.insert(0, editor_dir)

try:
    from editor.ui.app import CMDAIEditorApp
except Exception:
    from ui.app import CMDAIEditorApp

def main():

    from editor.core.workspace import projects_root
    from editor.ui.project_picker import ProjectPicker

    flagi, pozycyjne = set(), []
    for arg in sys.argv[1:]:
        if arg.startswith("-"):
            flagi.add(arg)
        else:
            pozycyjne.append(arg)

    if pozycyjne:
        target = pozycyjne[0]
    elif os.path.basename(os.path.abspath(".")).lower() == "editor":
        target = project_root
    else:
        target = None

    if target is None:

        if "--no-picker" in flagi:
            target = "."
        else:

            try:
                projects_root().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"Cannot create projects folder: {exc}",
                      file=sys.stderr)
                return
            from textual.app import App

            class _Launcher(App):

                def on_mount(self) -> None:
                    self.push_screen(ProjectPicker(), self._got_path)

                def _got_path(self, sciezka) -> None:
                    if not sciezka:
                        self.exit()
                        return
                    edytor = CMDAIEditorApp(initial_path=sciezka)
                    self.exit()
                    edytor.run()

            _Launcher().run()
            return

    abs_target = os.path.abspath(target)
    app = CMDAIEditorApp(initial_path=abs_target)
    app.run()

if __name__ == "__main__":
    main()
