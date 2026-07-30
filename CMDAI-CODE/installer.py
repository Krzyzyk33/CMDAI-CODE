import os
import sys
import time
import subprocess
from typing import List, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from rich.console import Console
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
    from rich.markup import escape as esc
    console = Console()
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "pyfiglet"], check=True)
    from rich.console import Console
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
    from rich.markup import escape as esc
    console = Console()

MUTED_COLOR = "bright_black"

MAIN_MODELS = [
    {
        "name": "Qwen 2.5 Coder 7B Instruct (Q4_K_M ~4.7 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    },
    {
        "name": "Llama 3.1 8B Instruct (Q4_K_M ~4.9 GB)",
        "url": "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
    },
    {
        "name": "Gemma 2 9B Instruct (Q4_K_M ~5.8 GB)",
        "url": "https://huggingface.co/bartowski/gemma-2-9b-it-GGUF/resolve/main/gemma-2-9b-it-Q4_K_M.gguf",
        "file": "gemma-2-9b-it-Q4_K_M.gguf",
    },
    {
        "name": "DeepSeek R1 Distill Qwen 8B (Q4_K_M ~4.9 GB)",
        "url": "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-8B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-8B-Q4_K_M.gguf",
        "file": "DeepSeek-R1-Distill-Qwen-8B-Q4_K_M.gguf",
    },
    {
        "name": "Phi-3.5 Mini 3.8B Instruct (Q4_K_M ~2.4 GB)",
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "file": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
    },
    {
        "name": "Qwen 2.5 14B Instruct (Q4_K_M ~9.0 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF/resolve/main/qwen2.5-14b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-14b-instruct-q4_k_m.gguf",
    },
    {
        "name": "DeepSeek R1 Distill Qwen 14B (Q4_K_M ~9.0 GB)",
        "url": "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
        "file": "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
    },
    {
        "name": "Gemma 2 2B Instruct (Q4_K_M ~1.6 GB)",
        "url": "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
        "file": "gemma-2-2b-it-Q4_K_M.gguf",
    },
    {
        "name": "Mistral 7B Instruct v0.3 (Q4_K_M ~4.4 GB)",
        "url": "https://huggingface.co/bartowski/Mistral-7B-Instruct-v0.3-GGUF/resolve/main/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "file": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
    },
    {
        "name": "Codestral 22B v0.1 (Q4_K_M ~13.5 GB)",
        "url": "https://huggingface.co/bartowski/Codestral-22B-v0.1-GGUF/resolve/main/Codestral-22B-v0.1-Q4_K_M.gguf",
        "file": "Codestral-22B-v0.1-Q4_K_M.gguf",
    },
    {
        "name": "[ Skip ] - Do not download model now",
        "url": None,
        "file": None,
    },
]

SYSTEM_MODELS = [
    {
        "name": "Qwen 2.5 1.5B Instruct (Q4_K_M ~1.1 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    },
    {
        "name": "Qwen 2.5 0.5B Instruct (Q4_K_M ~0.4 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    },
    {
        "name": "Llama 3.2 1B Instruct (Q4_K_M ~0.8 GB)",
        "url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    },
    {
        "name": "Llama 3.2 3B Instruct (Q4_K_M ~2.0 GB)",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    },
    {
        "name": "Gemma 2 2B Instruct (Q4_K_M ~1.6 GB)",
        "url": "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
        "file": "gemma-2-2b-it-Q4_K_M.gguf",
    },
    {
        "name": "SmolLM2 1.7B Instruct (Q4_K_M ~1.0 GB)",
        "url": "https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF/resolve/main/smollm2-1.7b-instruct-q4_k_m.gguf",
        "file": "smollm2-1.7b-instruct-q4_k_m.gguf",
    },
    {
        "name": "SmolLM2 360M Instruct (Q4_K_M ~0.2 GB)",
        "url": "https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct-GGUF/resolve/main/smollm2-360m-instruct-q4_k_m.gguf",
        "file": "smollm2-360m-instruct-q4_k_m.gguf",
    },
    {
        "name": "Qwen 2.5 3B Instruct (Q4_K_M ~2.0 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
    },
    {
        "name": "Phi-3.5 Mini 3.8B Instruct (Q4_K_M ~2.4 GB)",
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "file": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
    },
    {
        "name": "DeepSeek R1 Distill Qwen 1.5B (Q4_K_M ~1.1 GB)",
        "url": "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
        "file": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
    },
    {
        "name": "[ Skip ] - Do not download system model now",
        "url": None,
        "file": None,
    },
]


def print_app_header():
    os.system("cls" if os.name == "nt" else "clear")
    import shutil
    from datetime import date
    from itertools import zip_longest

    WHITE = "#ffffff"
    GRAY = "#888888"
    GAP = "   "

    day_of_year = date.today().timetuple().tm_yday
    cycle = day_of_year % 4
    if cycle == 0:
        cmdai_color, code_color = WHITE, WHITE
    elif cycle == 1:
        cmdai_color, code_color = GRAY, WHITE
    elif cycle == 2:
        cmdai_color, code_color = WHITE, GRAY
    else:
        cmdai_color, code_color = GRAY, GRAY

    cmdai_lines, code_lines = [], []
    try:
        import pyfiglet
        cmdai_lines = pyfiglet.figlet_format("CMDAI", font="ansi_shadow").split("\n")
        code_lines = pyfiglet.figlet_format("CODE", font="ansi_shadow").split("\n")
        
        while cmdai_lines and cmdai_lines[-1].strip() == "" and code_lines and code_lines[-1].strip() == "":
            cmdai_lines.pop()
            code_lines.pop()
            
        has_pyfiglet = True
    except Exception:
        has_pyfiglet = False
        cmdai_lines, code_lines = [], []

    term_width = shutil.get_terminal_size().columns
    header_text = Text()

    if has_pyfiglet:
        max_cmdai_width = max((len(l) for l in cmdai_lines), default=0)
        max_code_width = max((len(l) for l in code_lines), default=0)
        
        full_row1 = max_cmdai_width + len(GAP) + max_code_width
        padding1 = max(0, (term_width - full_row1) // 2)
        
        for c_line, cd_line in zip_longest(cmdai_lines, code_lines, fillvalue=""):
            header_text.append(" " * padding1)
            header_text.append(c_line.ljust(max_cmdai_width), style=cmdai_color)
            header_text.append(GAP)
            header_text.append(cd_line, style=code_color)
            header_text.append("\n")
    else:
        ascii_logo_top = [
            " ███ ██ ██ ██  ███ ███   ███ ███ ██  ███",
            " █   █ █ █ █ █ █ █  █    █   █ █ █ █ █  ",
            " █   █   █ █ █ ███  █    █   █ █ █ █ ███",
            " █   █   █ █ █ █ █  █    █   █ █ █ █ █  ",
            " ███ █   █ ██  █ █ ███   ███ ███ ██  ███",
        ]
        max_w1 = max(len(l) for l in ascii_logo_top)
        p1 = max(0, (term_width - max_w1) // 2)
        
        for line in ascii_logo_top:
            header_text.append(" " * p1 + line + "\n", style=WHITE)

    console.print(header_text)
    console.print()


def run_continuous_menu(title_str: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    selected_idx = 0
    num_items = len(items)

    def render_block(idx):
        lines = []
        lines.append(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        lines.append(f"[{MUTED_COLOR}]│ ✻ [bold white]{title_str}[/bold white][/{MUTED_COLOR}]")
        lines.append(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        lines.append(f"[{MUTED_COLOR}]│  [bright_black](Navigation: UP/DOWN Arrows, Select: ENTER)[/bright_black][/{MUTED_COLOR}]")
        lines.append(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")
        for i, item in enumerate(items):
            if i == idx:
                lines.append(f"[{MUTED_COLOR}]│   [bold white]➜ {item['name']}[/bold white][/{MUTED_COLOR}]")
            else:
                lines.append(f"[{MUTED_COLOR}]│     [bright_black]{item['name']}[/bright_black][/{MUTED_COLOR}]")
        return lines

    initial_lines = render_block(selected_idx)
    for l in initial_lines:
        console.print(l)

    total_lines = len(initial_lines)

    try:
        import msvcrt
        while True:
            key = msvcrt.getch()
            if key in (b'\x00', b'\xe0'):
                sub_key = msvcrt.getch()
                if sub_key == b'H':  # Up
                    selected_idx = (selected_idx - 1) % num_items
                elif sub_key == b'P':  # Down
                    selected_idx = (selected_idx + 1) % num_items
                else:
                    continue
            elif key in (b'\r', b'\n'):
                break
            elif key in (b'\x03', b'q', b'Q'):  # Ctrl+C or Q
                selected_idx = num_items - 1
                break
            else:
                continue

            # Redraw block in-place with ANSI cursor up
            sys.stdout.write(f"\033[{total_lines}A\033[0J")
            sys.stdout.flush()
            new_lines = render_block(selected_idx)
            for l in new_lines:
                console.print(l)
    except Exception:
        pass

    return items[selected_idx]


def download_file_with_progress(url: str, dest_path: str, label: str):
    import urllib.request

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    if os.path.exists(dest_path):
        console.print(f"[{MUTED_COLOR}]│   ⎿  File {os.path.basename(dest_path)} already exists, skipping download.[/{MUTED_COLOR}]")
        return

    console.print(f"[{MUTED_COLOR}]│ ✻ Downloading {label}...[/{MUTED_COLOR}]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold white]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(f"Downloading", total=None)

        def req_hook(block_num, block_size, total_size):
            if total_size > 0:
                progress.update(task_id, total=total_size, completed=block_num * block_size)

        try:
            urllib.request.urlretrieve(url, dest_path, reporthook=req_hook)
            console.print(f"[{MUTED_COLOR}]│   ⎿  Downloaded successfully: {os.path.basename(dest_path)}[/{MUTED_COLOR}]")
        except Exception as e:
            console.print(f"[red]│   ✗ Download error: {e}[/red]")


def main():
    print_app_header()
    
    # 2 second delay
    time.sleep(2.0)

    console.print(f"[{MUTED_COLOR}]╭ Setup Installer[/{MUTED_COLOR}]")
    console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")

    root_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(root_dir, "requirements.txt")

    # Step 1: Install requirements
    console.print(f"[{MUTED_COLOR}]│ ✻ Installing Python core dependencies from requirements.txt...[/{MUTED_COLOR}]")
    if os.path.exists(req_file):
        cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            console.print(f"[{MUTED_COLOR}]│   ⎿  Completed (requirements.txt packages updated)[/{MUTED_COLOR}]")
        else:
            console.print(f"[{MUTED_COLOR}]│   ⎿  Warning during pip install: {res.stderr[:200]}[/{MUTED_COLOR}]")

    console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")

    # Step 2: Install package editable
    console.print(f"[{MUTED_COLOR}]│ ✻ Registering cmdai-code package executable...[/{MUTED_COLOR}]")
    res_pkg = subprocess.run([sys.executable, "-m", "pip", "install", "-e", root_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_pkg.returncode == 0:
        console.print(f"[{MUTED_COLOR}]│   ⎿  Completed (cmdai-code registered)[/{MUTED_COLOR}]")

    console.print(f"[{MUTED_COLOR}]│[/{MUTED_COLOR}]")

    # Step 3: Configure PATH
    console.print(f"[{MUTED_COLOR}]│ ✻ Configuring System User PATH environment variable...[/{MUTED_COLOR}]")
    ps_script = os.path.join(root_dir, "update_path.ps1")
    if os.path.exists(ps_script):
        subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    console.print(f"[{MUTED_COLOR}]│   ⎿  Completed (Added {root_dir} to PATH)[/{MUTED_COLOR}]")

    # Step 4: Choose Main LLM Model
    main_choice = run_continuous_menu("Select Main LLM Model: ", MAIN_MODELS)
    if main_choice and main_choice.get("url"):
        dest = os.path.join(root_dir, "models", main_choice["file"])
        download_file_with_progress(main_choice["url"], dest, main_choice["name"])

    # Step 5: Choose System / Compaction Model
    sys_choice = run_continuous_menu("Select System Model: ", SYSTEM_MODELS)
    if sys_choice and sys_choice.get("url"):
        dest = os.path.join(root_dir, "systemmodels", sys_choice["file"])
        download_file_with_progress(sys_choice["url"], dest, sys_choice["name"])

    # Close single continuous block cleanly
    console.print(f"[{MUTED_COLOR}]╰──────────────────────────────────────────────────────────────────────────[/{MUTED_COLOR}]\n")

    # Final summary
    console.print(f"\n[bold white]CMDAI CODE installation and configuration completed successfully![/bold white]")
    console.print(f"[{MUTED_COLOR}]Run the application anytime from any terminal by typing:[/{MUTED_COLOR}] [bold white]cmdai-code or cmdai code[/bold white]\n")

if __name__ == "__main__":
    main()
