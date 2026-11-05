# Plan Poprawy Architektury CMDAI CODE (`planpoprawy.md`)

Niniejszy dokument definiuje harmonogram prac krok po kroku nad modernizacją silnika CMDAI CODE. Każdy krok jest testowany przed przejściem do kolejnego.

---

## Postęp prac

- [x] **Krok 1: Ujednolicenie Entrypointu (`cmdai.py` vs `src/cmdai/cli.py`)**
  - [x] Usunięcie duplikacji logiki w `cmdai.py` (lekki shim importujący `cmdai.cli:main`)
  - [x] Naprawienie routingu `cmdai editor` w `cli.py` (usunięcie odwołania do skasowanego `editor_app.py`, przejście na `_cmdaieditor_main()`)
  - [x] Weryfikacja działania `python cmdai.py --help` i obsługi poleceń

- [x] **Krok 2: Obsługa reguł projektu (`AGENTS.md`)**
  - [x] Dodanie automatycznego odczytu `AGENTS.md` (oraz fallback do `CLAUDE.md`, `.cursorrules`) z katalogu roboczego (`workdir`)
  - [x] Wstrzyknięcie zawartości `AGENTS.md` do promptu systemowego agenta w `runner.py`
  - [x] Rozszerzenie komendy `/init` o generowanie szablonu `AGENTS.md` w projekcie
  - [x] Testy wczytywania reguł w sesji agenta zakończone sukcesem

- [x] **Krok 3: Automatyczna diagnostyka LSP po edycji (`write` / `edit`)**
  - [x] Podłączenie `run_file_diagnostics` z `agent/lsp_client.py` bezpośrednio do wyniku narzędzi `write` i `edit` w `agent/tools.py`
  - [x] Zwracanie ewentualnych błędów składni (syntax errors / linter) natychmiast do kontekstu modelu
  - [x] Test samoczynnego poprawiania błędów składniowych przez model w tej samej turze (zwracanie dyrektywy naprawczej LSP `ACTION REQUIRED: Review and fix the diagnostic issues...`)

- [x] **Krok 4: Granulowane uprawnienia w `config.json`**
  - [x] Zastąpienie sztywnego `mode: ask | auto` hierarchiczną strukturą reguł uprawnień w konfiguracji (`DEFAULT_CONFIG["permissions"]` w `settings.py`)
  - [x] Obsługa wzorców dopasowania (np. `"command": { "git status*": "allow", "rm*": "ask" }`) przy pomocy `fnmatch`
  - [x] Bezpieczne automatyczne zatwierdzanie operacji tylko do odczytu i selektywne blokowanie/pytanie przy poleceniach niebezpiecznych

- [x] **Krok 5: Natywny Tool-Calling + Centralny Rejestr Narzędzi**
  - [x] Utworzenie `tool_specs.py` z centralnym rejestrem definicji JSON Schema dla wszystkich narzędzi
  - [x] Implementacja `stream_chat_completion(..., tools=...)` oraz akumulatora `tool_calls` dla providerów API (OpenAI, Anthropic, Gemini, OpenRouter)
  - [x] Zachowanie parsera XML/regex jako wstecznie kompatybilnego fallbacku dla modeli lokalnych GGUF
  - [x] Likwidacja przedwczesnego zrywania streamingu i uciętych odpowiedzi przy wywołaniach natywnych narzędzi

- [x] **Krok 6: Wyciągnięcie `SessionRuntime` z `tui/app.py`**
  - [x] Wydzielenie stanu sesji, historii wiadomości i pętli agenta do niezależnej klasy `SessionRuntime` (`src/cmdai/core/session_runtime.py`)
  - [x] Odchudzenie zależności tak, aby logika agenta mogła działać niezależnie od Textual UI
  - [x] Umożliwienie bezgłowego uruchamiania zadań (`cmdai run <prompt>`) w terminalu bez konieczności uruchamiania pełnego interfejsu TUI

---

## Dziennik zmian i wdrożeń

* **Krok 1 zakończony sukcesem:** `cmdai.py` jest teraz oficjalnym, czystym punktem startowym delegującym do `src/cmdai/cli.py`. Wyeliminowano stary błąd `ModuleNotFoundError: No module named 'cmdai.editor.editor_app'` przy uruchamianiu edytora z poziomu CLI.
* **Krok 2 zakończony sukcesem:** Zaimplementowano wczytywanie reguł z `AGENTS.md` (lub `.cursorrules`/`CLAUDE.md`). Zawartość reguł projektu trafia bezpośrednio do promptu agenta w każdej turze. Komenda `/init` oraz pomoc w `/help` zostały zintegrowane.
* **Krok 3 zakończony sukcesem:** Zintegrowano diagnostykę LSP bezpośrednio z operacjami edycji i zapisu (`tool_edit`, `tool_write`). Ewentualne ostrzeżenia i błędy lintera/LSP są od razu dołączane do odpowiedzi narzędzia z jasną instrukcją `ACTION REQUIRED`, dzięki czemu agent natychmiast poprawia własne błędy składniowe i typowania bez konieczności ręcznego uruchamiania `/bugs`.
* **Krok 4 zakończony sukcesem:** Zaimplementowano granulowane uprawnienia w `config.json` / `settings.py` oraz ewaluację w `runner.py:check_tool_permission`. Zamiast binarnego wyboru trybu `ask/auto`, narzędzia (w tym polecenia terminalowe) można filtrować wzorcami (np. automatyczne zezwolenie dla `git status*` i `git diff*`, pytania dla `rm*`, blokowanie `deny`).
* **Krok 5 zakończony sukcesem:** Utworzono `src/cmdai/agent/tool_specs.py` z pełnymi schematami JSON Schema dla wszystkich narzędzi. Zintegrowano natywny tool-calling w `stream_chat_completion` dla providerów API (OpenAI, Anthropic, Gemini, OpenRouter), eliminując przedwczesne ucinanie streamingu i zachowując parser XML jako fallback dla modeli lokalnych GGUF.
* **Krok 6 zakończony sukcesem:** Zaimplementowano `SessionRuntime` w `src/cmdai/core/session_runtime.py`. Silnik agenta, transkrypcja i pętla narzędziowa są teraz całkowicie niezależne od Textual UI. Dodano obsługę bezgłowego wywoływania zadań przez CLI: `cmdai run <prompt>`, które strumieniuje odpowiedź modelu i wykonuje narzędzia bezpośrednio w standardowym strumieniu konsoli.
