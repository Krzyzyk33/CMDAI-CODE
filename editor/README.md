# CMDAI EDITOR

Kodowy edytor terminalowy zbudowany na [Textual](https://textual.textualize.io).
Osobna aplikacja — nie jest częścią CMDAI, choć korzysta z trzech
modułów tego repo (patrz [Zależność od CMDAI](#zależność--od-cmdai)).

```
  ██████╗ ███╗   ███╗ ██████╗   █████╗  ██╗        
 ██╔════╝ ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║        
 ██║      ██╔████╔██║ ██║  ██║ ███████║ ██║        
 ██║      ██║╚██╔╝██║ ██║  ██║ ██╔══██║ ██║        
 ╚██████╗ ██║ ╚═╝ ██║ ██████╔╝ ██║  ██║ ██║        
  ╚═════╝ ╚═╝     ╚═╝ ╚═════╝  ╚═╝  ╚═╝ ╚═╝        
 ███████╗ ██████╗  ██╗ ████████╗  ██████╗  ██████╗ 
 ██╔════╝ ██╔══██╗ ██║ ╚══██╔══╝ ██╔═══██╗ ██╔══██╗
 █████╗   ██║  ██║ ██║    ██║    ██║   ██║ ██████╔╝
 ██╔══╝   ██║  ██║ ██║    ██║    ██║   ██║ ██╔══██╗
 ███████╗ ██████╔╝ ██║    ██║    ╚██████╔╝ ██║  ██║
 ╚══════╝ ╚═════╝  ╚═╝    ╚═╝     ╚═════╝  ╚═╝  ╚═╝
```

## Instalacja

### Lokalnie

```powershell
pip install -r editor/requirements-editor.txt
python -m editor.main <katalog>
```

Albo gotowy skrypt, który sprawdza Pythona, instaluje zależności
i przygotowuje katalog roboczy:

```powershell
editor\install.bat
```

### Zdalnie, jedną komendą

Z dowolnego katalogu — klonuje repo, instaluje zależności
edytora i przygotowuje katalog roboczy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/editor/tools/install-remote.ps1 | iex"
```

Skrypt [`tools/install-remote.ps1`](tools/install-remote.ps1) klonuje
repo do `~/CMDAI-CODE` (lub do `-Root <ścieżka>`) i przy ponownym
uruchomieniu robi `git pull`. Używa `editor\requirements-editor.txt`,
**nie** `requirements.txt` aplikacji, i nie tworzy `config.json` —
to plik aplikacji CMDAI.

### Co jest potrzebne

Python 3.10+. Zależności to 15 bibliotek, **wszystkie permissive**
(14× MIT, plus `fonttools` tylko do regeneracji fontu ikon). Żadna nie
jest copyleft. Pełne zestawienie z wersjami i wymaganymi tekstami
licencji: [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

Opcjonalnie **`ruff`** — bez niego PROBLEMS pokazuje tylko błędy
składni, bez reguł `F` (niezdefiniowane nazwy, nieuzywane importy).
Instalator ostrzega, jeśli go brakuje, ale nie przerywa instalacji.

> `requirements.txt` w korzeniu należy do aplikacji CMDAI i zawiera
> `pydantic`, `psutil`, `gguf`, `requests`, `art`. **Edytor nie używa
> żadnej z nich** — do edytora wystarczy `requirements-editor.txt`.

### Uruchomienie

```powershell
editor\run.bat                  # otwiera folder editor\
editor\run.bat C:\moj\projekt  # otwiera konkretny katalog
python -m editor.main           # z katalogu repo
```

### `cmdai editor`

```powershell
cmdai editor                    # ekran wyboru projektu
cmdai editor C:\moj\projekt    # od razu otwiera folder
```

Bez argumentu `cmdai editor` pokazuje **ekran wyboru**:

- **lista projektów** z `editor/projects/` — pozycja w liście to
  projekt utworzony wcześniej przez ten ekran,
- **New project** — nowy folder `editor/projects/<nazwa>` razem z
  `.cmdai_code_project/checkpoints` (nazwy z `/ \ : * ? " < > |`,
  z kropką na początku i nazwy urządzeń `CON`/`NUL`/`COM1` są
  odrzucane, więc nie da się utworzyć folderu, którego Windows
  by nie pokazał),
- **Open folder** — dowolny katalog z dysku.

`Esc` wraca na listę, a z listy zamyka wybór. Z katalogu
`editor/` (czyli z `editor\run.bat` bez argumentu) pytanie się nie
pokazuje i otwiera się repo — inaczej uruchomienie z własnego
folderu edytora kończyłoby się pętłą pytania.

Zawartość `editor/projects/` jest w `.gitignore` (`projects/*` +
`!projects/.gitkeep`): to kod użytkownika, nie część edytora.
Sama aplikacja CMDAI CODE ma **własną** komendę `cmdai code editor`
i własny edytor — ta komenda działa dokładnie jak przedtem,
`cmdai editor` to osobna, dodana gałąź.

## Skróty klawiaturowe w dialogach

Otwarty dialog jest **modalny na całym terminalu** — klawiatura trafia
w jego pole, dopóki jest otwarty:

- `App.on_key` **nie wystarcza** do tego. Sprawdzone eksperymentem:
  przy fokusie na `TextArea` litera trafia do bufora tekstu, a
  `App.on_key` nie widzi jej w ogóle — `TextArea` ją zjada. Dlatego
  `CMDAIEditorApp.on_event` przechwytuje zdarzenie **powyżej**
  dispatchu do zogniskowanego widgetu.
- Nie dublujemy zdarzeń przez `post_message` — `Input._on_key`
  obsługuje tylko znaki drukowalne, a `backspace`, strzałki, `enter`
  i `ctrl+a` są w `Input.BINDINGS`, a bindingi rozwiązuje ten pump,
  który ma fokus. Zamiast tego `on_event` przenosi **faktyczny
  fokus** przez `Screen.set_focus`, który przypisuje `self.focused`
  synchronicznie (`Widget.focus()` idzie przez `on_callback` i działa
  o jedną wiadomość później).
- `escape` i `tab` są przepuszczane bez zmian: `escape` zamyka
  dialog, `tab` pozwala wskoczyć z pola na przycisk.
- Po zamknięciu dialogu fokus wraca do `#code-area`, a nie do tego,
  co było zaznaczone wcześniej — `Ctrl+N` da się wywołać z drzewa
  eksploratora i wtedy fokus wracałby do drzewa zamiast do
  edytora.

### Wysokość pól `Input`

`height` w Textualu dotyczy **wiersza treści**, nie całości — obwódka
dokłada się z osobna. `height: 1` + `border: round` daje więc
`container_size.height == 0`: ramka się rysuje, a tekst nie ma gdzie
trafić. Użytkownik widzi puste zaokrąglone pole i pisze „jakby nie ma
miejsca do pisania" — wartość jest w buforze, ale jej nie widać.

W edytorze wszystkie pola mają `height: 3`, czyli jeden wiersz
treści. Sprawdza to `scan_input_heights.py` — mierzy
`container_size` każdego widocznego pola i pilnuje, żeby żadne nie
wróciło do zera.


## Projects

**Projects** — pierwsza pozycja w menu (logo `⌬ CMDAI EDITOR` w lewym
górnym rogu), nad „New File". Ikona `⊞` (U+229E,
`east_asian_width = "N"`), a nie `+` — `+` już oznacza nowy plik.

Ekran zajmuje **całą powierzchnię** i jest podzielony na dwie kolumny
(2fr / 3fr, czyli prawa ma trochę więcej niż połowę):

| | |
|---|---|
| **LEWA** | **EXISTING** — projekty, które już istnieją w `editor/projects/`. Strzałki + Enter otwierają zaznaczony. |
| **PRAWA** | **NEW PROJECT** — pole nazwy + `create`, a niżej `or open an existing folder` + `open`. |

Wybór otwiera projekt **w nowym oknie terminala** (`root_dir` i drzewo
eksploratora są ustawiane w `compose()`, więc przełączenie projektu w
żywej aplikacji wymagałoby przebudowania drzewa plików). Zajęta nazwa,
`CON`/`NUL`/`COM1` albo prefiks kropki → komunikat, bez tworzenia
czegokolwiek. `Esc` zamyka bez wyboru.

Ta sama lista i te same formularze obsługuje `cmdai editor` bez
argumentu.


## Skróty

| Klawisz | Działanie |
|---|---|
| `Ctrl+S` | Zapisz |
| `Ctrl+Shift+S` | Zapisz jako |
| `Ctrl+N` | Nowy plik |
| `Ctrl+W` | Zamknij zakładkę |
| `Ctrl+B` | Pokaż/ukryj eksplorator |
| `Ctrl+L` | Numery linii |
| ``Ctrl+` `` | Terminal (panel dolny) |
| `Ctrl+F5` | Uruchom aktywny plik |
| `Ctrl+Shift+F5` | Zatrzymaj |
| `Ctrl+G` | Gałęzie Git |
| `Ctrl+H` | Checkpointy |
| `Ctrl+Q` | Wyjście |
| `Esc` | Cofnij / oddaj fokus |

Przycisk **Run w prawym górnym rogu to przełącznik**: `▸` uruchamia,
`■` zatrzymuje. Samo `Ctrl+F5` też działa — a przy aktywnym procesie
`Ctrl+F5` zatrzymuje go, więc skrót i przycisk robią to samo.

## Panel dolny: TERMINAL i PROBLEMS

Panel ma dwie zakładki, przełączane kliknięciem w nagłówek albo
przełącznikiem `▴ PANEL` / `▾ PANEL` w pasku statusu.

**TERMINAL** — trwała sesja PowerShell w katalogu projektu. Wiersz
poleceń to zwykłe `Input`, więc działa autouzupełnianie historii.
`Esc` zamyka panel, nie terminal.

**PROBLEMS** — lista problemów w stylu VS Code:

- nagłówek pliku to przycisk: klik **zwija** grupę, a przy zwinięciu
  zostaje sama nazwa **z licznikami** (`x 3  ! 9`), więc wiadomo ile
  ma problemów bez rozwijania
- pliki z błędami idą na górze, potem te tylko z ostrzeżeniami
- nagłówek całej listy jest filtrem: klik przełącza **wszystkie /
  tylko błędy** — realny projekt daje ~60 ostrzeżeń `F401` na kilka
  błędów składni, warto je ukryć
- klik w problem **otwiera plik i stawia kursor dokładnie na tej
  linii**; wiersz z błędem ma czerwone tło, z ostrzeżeniem bursztynowe
- zakładka pokazuje **jedną liczbę** z znakiem ostrzegania, np.
  `PROBLEMS ‼ 18`. Podział na błędy i ostrzeżenia widać w nagłówkach
  grup i w pasku statusu (`x 7  ! 64`)

**Zasięg skanu to cały katalog projektu**, nie tylko aktywny plik —
plik, którego nie otwierałeś, też się tu pojawi. Skan startuje po
kliknięciu w zakładkę PROBLEMS, leci w wątku (nie blokuje interfejsu)
i pomija `node_modules`, `.git`, `__pycache__`, `venv` i inne śmietniki.
Dla aktywnego pliku liczą siędiagnostyka z **bufora**, więc widoczne są
błędy w niezapisanej wersji; wpisy z pliku na dysku dostają uzupełnione.

Składnia: **18 języków** przez `tree-sitter` (18 rozszerzeń, 10
gramatyk). Dodatkowo `ruff` dla Pythona: niezdefiniowane nazwy,
nieuzywane importy. Bez `ruff` działa sama składnia — pasek statusu
dopisze wtedy podpowiedź, żeby go doinstalować.

**Eksplorator** pokazuje pliki z błędem na **czerwono**, z ostrzeżeniem
na **żółto**. Tylko pliki, nie katalogi — katalog z problemami w
pięciu plikach byłby w całości czerwony, co nic nie mówi.

## Uruchamianie plików

`▸` w pasku tytułowym uruchamia aktywny plik, `■` go zatrzymuje.
Przed uruchomieniem plik jest automatycznie zapisywany.

Obsługiwane rozszerzenia (11): `.bash`, `.bat`, `.cjs`, `.cmd`, `.js`, `.jsx`, `.mjs`, `.ps1`, `.py`, `.pyw`, `.sh`. Pozostałe mają przycisk
wyszarzony — hover podaje powód (np. „`.json` is not directly runnable”).
`.bat`, `.cmd`, `.ps1`, `.sh`, `.bash`. Pozostałe mają przycisk
wyszarzony — hover podaje powód („`.json` is not directly runnable”).

Zaznaczony tekst w edytorze idzie do procesu jako argumenty, tak jak
w VS Code: zaznacz `print('x')` i uruchom, skrypt dostanie to w
`sys.argv[1]`.

Uruchamianie idzie przez **osobny, krótkotrwały proces**, a nie przez
sesję terminala. Powód: sesja na potoku nie da się przerwać — brak
PTY, więc `python` czekający na `input()` zablokowałby edytor na
stałe. Osobny proces daje pewny Stop (zmierzone: `sleep(300)` ubity
w ~260 ms).

## Pasek statusu

`▴ PANEL` · gałąź git · ikona pliku · `x 7 ! 64` · `Ln 1, Col 1` ·
`3113 lines` · `PYTHON` · kodowanie

Liczniki błędów mają **własny widget** przed pozycją kursora. Bez tego
przy 100 kolumnach `Ln 1, Col 1 • 3113 lines • PYTHON • x 7 ! 65 • UTF-8`
nie mieściło się i końcówka z licznikiem była obcinana — czyli
dokładnie to, po co pasek istnieje. Nazwa pliku ma `min-width` i
wygaszana jest wielokropkiem przy długiej nazwie.

## Przewijanie

Kod przewija się w poziomie (zawijanie linii wyłączone). Pasek
poziomy ma **1 komorkę** i pojawia się **tylko wtedy**, gdy jakaś
linia jest szersza niż widok — przy krótkich plikach nie zabiera
wiersza. Pionowy jest zawsze 1 kolumna. `End` i `Home` przewijają
widok za kursorem.

## Logo

Jedno logo, jedno miejsce: [`ui/logo_variants.py`](ui/logo_variants.py).
Ułożone pionowo — `CMDAI` nad `EDITOR` — bo w czcionce blokowej
`█ ═ ║ ╔ ╗ ╚ ╝` napis w jednej linii zajmował 98 kolumn i był ucięty
przy typowym terminalu. W pionie ma 51 kolumn i mieści się od około
90 kolumn szerokości okna; poniżej jest ucinany z prawej, ale nigdy
nie zawija się w poszatki.

Regeneracja (potrzebuje Node.js):

```powershell
python editor\tools\gen_stacked.py --write
```

Skrypt odpala `npx oh-my-logo` dla każdego słowa osobno i składa
wynik pionowo. Weryfikacja, że generator daje dokładnie to, co jest
w pliku: `python editor\tools\gen_stacked.py` i porównanie.

> Glify blokowe mają `east_asian_width = "A"` (Ambiguous). W terminalu
> zachodnim zajmują 1 komórkę i wyglądają dobrze. **W terminalu
> skonfigurowanym dla CJK zajmują 2 komorki i logo się rozjedzie.**
> `tools/gen_logo.py` to waliduje i czcionki blokowe odrzuca;
> `logo_variants.py` jest świadomym wyjątkiem.

## Font ikon

`icons/font/cmdai-icons.ttf` jest **generowany przez ten projekt** z
prymitywów geometrycznych (`icons/build_font.py`, `fontTools` — rysowanie
przez `moveTo`/`lineTo`). Żadnego obrysu glifu nie skopiowano z
istniejącego fontu, więc nie ciąży na nim licencja cudzego fontu.

```powershell
python -m editor.icons.build_font          # zbuduj
python editor\icons\install_font.py        # zainstaluj dla bieżącego użytkownika
```

Instalator kopiuje TTF do `%LOCALAPPDATA%\Microsoft\Windows\Fonts`,
dopisuje wpis w `HKCU\...\Fonts` i wysyła `WM_FONTCHANGE` — **bez
wymagań admina**, ale zapisuje w rejestrze. Poprzednie wersje fontu o
tej samej nazwie są usuwane, bo inaczej system trzymałby w pamięci
starszą.

Opcjonalnie edytor wykorzystuje glify **Nerd Fontu**, jeśli taki jest
zainstalowany (tryb ikon `nerd`). Żadnych danych Nerd Fontu nie
dołączamy ani nie dystrybuujemy — glify rysuje font, który użytkownik
już ma.

## Zależność od CMDAI

Edytor importuje **trzy moduły** z aplikacji CMDAI:

```
ui/app.py:57   from cmdai.core.checkpoints import CheckpointManager
ui/app.py:58   from cmdai.tui.modals.git_modal import GitBranchesModal
ui/app.py:59   from cmdai.tui.modals.checkpoints import CheckpointsModal
```

To zarządzanie checkpointami i okna Git / Checkpoints. **Edytor nie
jest w pełni samodzielny** — do tych funkcji potrzebuje `src/cmdai`
w tym samym repo. Reszta edytora jest samowystarczalna.

## Licencja

[`LICENSE.editor`](../LICENSE.editor) — własny kod edytora:
zabronione użycie komercyjne, redystrybucja i forki. Zależności
zewnętrzne pozostają na swoich licencjach i ta licencja ich nie
ogranicza (paragraf 6). Zestawienie: [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Testy

Katalog `editor/tests/` zawiera `test_editor_launch.py` — sprawdza, że
aplikacja się montuje i `Pilot` odpowiada. To jest **cała** zawartość
tego katalogu; pełna weryfikacja funkcji (panel, PROBLEMS, Run, kliknięcia,
pasek poziomy) nie jest w repo.
