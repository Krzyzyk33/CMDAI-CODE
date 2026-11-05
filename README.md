<div align="center">
  <img src="assets/banner.png" alt="CMDAI CODE Banner" width="100%" />

  <p align="center">
    <a href="https://github.com/Krzyzyk33/CMDAI-CODE/releases"><img src="https://img.shields.io/badge/Release-v3.0.6--alpha-blue.svg?style=flat-square" alt="Release v3.0.6-alpha"></a>
    <a href="#"><img src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg?style=flat-square&logo=windows" alt="Platform: Windows"></a>
    <a href="#"><img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python" alt="Python 3.10+"></a>
    <a href="#"><img src="https://img.shields.io/badge/Local%20Inference-GGUF%20%2F%20llama.cpp-brightgreen.svg?style=flat-square" alt="llama.cpp"></a>
    <a href="#"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"></a>
  </p>

```text
 ██████╗███╗   ███╗██████╗  █████╗ ██╗    ██████╗ ██████╗ ██████╗ ███████╗
██╔════╝████╗ ████║██╔══██╗██╔══██╗██║   ██╔════╝██╔═══██╗██╔══██╗██╔════╝
██║     ██╔████╔██║██║  ██║███████║██║   ██║     ██║   ██║██║  ██║█████╗  
██║     ██║╚██╔╝██║██║  ██║██╔══██║██║   ██║     ██║   ██║██║  ██║██╔══╝  
╚██████╗██║ ╚═╝ ██║██████╔╝██║  ██║██║   ╚██████╗╚██████╔╝██████╔╝███████╗
 ╚═════╝╚═╝     ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝    ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
```

# CMDAI CODE
### Autonomous Terminal Coding Agent for Windows
*Powered by local GGUF models (llama.cpp) and multi-provider cloud AI.*

</div>

---

## 🌟 O projekcie

**CMDAI CODE** to autonomiczny agent programistyczny działający bezpośrednio w konsoli Windows (Windows Terminal & PowerShell). Zaprojektowany od podstaw z myślą o pełnej prywatności, elastyczności i wydajności:
- **Local-First**: Natywna obsługa modeli kwantyzowanych `.gguf` za pomocą `llama.cpp` z akceleracją Vulkan / CPU – praca całkowicie offline bez wysyłania kodu na zewnętrzne serwery.
- **Multi-Provider Cloud**: Bezproblemowa integracja z czołowymi dostawcami AI (OpenCode Zen, OpenRouter, Anthropic Claude, OpenAI, Google Gemini, Ollama, vLLM oraz dowolnymi niestandardowymi endpointami kompatybilnymi z OpenAI API).
- **Autonomiczne Subagenty i Tło**: Możliwość delegowania zadań wyspecjalizowanym subagentom (Code Reviewer, Tester, Refactor) pracującym synchronicznie lub w tle (`Ctrl+B` / `/bg`).
- **Nowoczesne TUI**: Dopracowany, minimalistyczny interfejs w stylu GitHub Dark oparty na bibliotece Textual – z podglądem kolorowych diffów, menedżerem sesji i natywną obsługą protokołu MCP (Model Context Protocol).

---

## 🚀 Kluczowe Funkcjonalności

- ⚡ **Local GGUF (Vulkan / CPU)**: Automatyczne wykrywanie i ładowanie modeli `.gguf` z folderu `models/`. Płynne generowanie tokenów bez konieczności konfiguracji zewnętrznych baz danych.
- 🌐 **Dodawanie Własnych Providerów**: Wbudowany kreator niestandardowych dostawców AI w `/models` (`Ctrl+A`) – wsparcie dla dowolnego serwera zgodnego z OpenAI API (Groq, Together, DeepInfra, prywatne serwery vLLM).
- 🗂️ **Zaawansowany Menedżer Sesji (`/sessions`)**: Zapisywanie pełnej historii konwersacji, automatyczne nadawanie czytelnych tytułów przez model AI, wyszukiwanie w czasie rzeczywistym oraz bezpieczne przełączanie kontekstów.
- ⚙️ **Zadania w Tle (`/bg`)**: Podgląd postępu i logów procesów oraz subagentów w czasie rzeczywistym bez blokowania terminala.
- 🔍 **Wbudowany Diff Viewer (`/diff`)**: Kolorowy, przejrzysty podgląd proponowanych modyfikacji kodu w plikach z możliwością zatwierdzenia lub odrzucenia zmian.
- 🔌 **Model Context Protocol (MCP)**: Rozszerzalność o zewnętrzne narzędzia i serwery MCP.

---

## 📥 Szybki Start (Instalacja)

### Wymagania:
- System: Windows 10 lub Windows 11 (zalecany Windows Terminal)
- Python 3.10+ (wraz z `pip`)
- Git

### Uruchomienie:
```powershell
# 1. Sklonuj repozytorium
git clone https://github.com/Krzyzyk33/CMDAI-CODE.git
cd CMDAI-CODE

# 2. Uruchom launcher (automatycznie konfiguruje venv i zależności)
.\cmdai.bat
```

Globalny skrót w systemie: aplikacja może zostać zainstalowana globalnie, co umożliwia uruchamianie poleceniem `cmdai` w dowolnym katalogu projektu.

---

## ⌨️ Skróty Klawiszowe i Polecenia Slash

### Główne Polecenia w Terminalu:
| Polecenie | Opis działania |
| :--- | :--- |
| `/models` | Wybór aktywnego modelu lokalnego GGUF lub dostawcy chmurowego |
| `/sessions` | Przeglądarka i menedżer zapisanych sesji z wyszukiwarką |
| `/bg` | Monitor procesów i subagentów pracujących w tle |
| `/mode` | Przełączanie trybu działania agenta (`auto`, `code`, `plan`, `ask`) |
| `/diff` | Podgląd zmian wprowadzonych w plikach z zatwierdzaniem |
| `/plan` | Interaktywny edytor i podgląd planu zadań |
| `/context` | Sprawdzanie i przypinanie plików do pamięci podręcznej agenta |
| `/help` | Kompletna lista skrótów i dokumentacja podręczna |

### Skróty w Interfejsie TUI:
- **`Enter`**: Wysłanie promptu / wybór opcji w menu
- **`Shift+Enter`**: Nowa linia w polu tekstowym
- **`Esc`**: Zamknięcie aktywnego modala / anulowanie generowania
- **`Ctrl+A`**: W `/models` – dodanie nowego providera AI; w liście modeli – dodanie modelu
- **`Ctrl+D`**: Konfiguracja klucza API dla wybranego dostawcy
- **`Ctrl+B`**: Wysłanie bieżącego zadania subagenta do pracy w tle

---

## 🛠️ Architektura Projektu

```text
CMDAI-CODE/
├── cmdai.py                  # Główny punkt wejścia do aplikacji
├── cmdai.bat                 # Launcher wsadowy dla Windows
├── assets/                   # Banery i zasoby graficzne
├── src/cmdai/
│   ├── agent/                # Pętla agenta, egzekutor narzędzi, subagenty i zadania tła
│   ├── core/                 # Zarządzanie sesjami, konfiguracja, katalog modeli i providerów
│   ├── editor/               # Wbudowany edytor plików i moduł diffów
│   ├── mcp/                  # Natywny klient Model Context Protocol
│   └── tui/                  # Interfejs konsolowy (Textual): ekrany, modale, style Dark
└── models/                   # Folder na lokalne wagi modeli .gguf (opcjonalny)
```

---

## 📄 Licencja

Projekt dystrybuowany jest na licencji MIT. Szczegóły w pliku [LICENSE](LICENSE).
Wersja: **v3.0.6-alpha**
