"""Swift-identical Pitch Black CSS Styles for CMDAI CODE."""

SWIFT_CSS = """
Screen {
    background: #000000;
    color: #e6edf3;
    padding: 0;
    margin: 0;
    width: 100%;
    height: 100%;
}

.hidden {
    display: none !important;
}

/* ═════════════════════════════════════════════════════════════
   1. HERO WELCOME VIEW (Full Size Logo Above Centered Input)
   ═════════════════════════════════════════════════════════════ */
#hero-view {
    width: 100%;
    height: 100%;
    align: center middle;
    background: #000000;
    display: block;
    padding: 0;
    margin: 0;
}

#hero-view.hidden {
    display: none !important;
}

#hero-card {
    width: 80;
    max-width: 95%;
    height: auto;
    align: center middle;
    margin: 0 1;
}

#hero-logo {
    width: 100%;
    text-align: center;
    content-align: center middle;
    margin-bottom: 1;
}

#hero-version {
    width: 100%;
    height: 0;
    display: none;
}

#hero-input-box {
    width: 100%;
    height: auto;
    background: #161b22;
    border: none;
    padding: 0;
    margin-bottom: 1;
}

#hero-prompt-input, #hero-prompt-input:focus, #hero-prompt-input.-focus {
    background: #161b22;
    border: none;
    outline: none;
    padding: 1 3 1 3;
    height: 3;
    min-height: 3;
    max-height: 8;
    color: #e6edf3;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#hero-meta-bar {
    height: auto;
    padding: 0 3 1 3;
    background: #161b22;
    color: #8b949e;
}

#hero-meta-left {
    width: auto;
    color: #58a6ff;
    margin-right: 2;
}

#hero-meta-mode {
    width: auto;
    margin-right: 2;
}

#hero-meta-thinking {
    width: auto;
    margin-right: 2;
}

#hero-meta-spacer {
    width: 1fr;
}

#hero-meta-right {
    width: auto;
    color: #484f58;
    text-align: right;
}

/* ═════════════════════════════════════════════════════════════
   2. CHAT VIEW (Active Conversation - Symmetric Padding)
   ═════════════════════════════════════════════════════════════ */
#chat-view {
    width: 100%;
    height: 100%;
    background: #000000;
    display: block;
    padding: 0;
    margin: 0;
}

#chat-view.hidden {
    display: none !important;
}

#chat-scroll {
    background: #000000;
    /* Left gutter 2, right 0. Was 2 on both sides, which left the cards one
       column narrower than the input below them and made the right margin read
       as 2 over one card and 1 over the other. */
    padding: 0 0 0 2;
    margin: 0;
    width: 100%;
    height: 1fr;
    /* Mouse wheel and keyboard scrolling stay enabled; the rail itself is
       intentionally hidden to keep the chat visually clean. */
    scrollbar-size-vertical: 0;
    scrollbar-gutter: auto;
}

/* User card: one empty row above the label, one between label and prompt,
   one below the prompt. */
.user-card {
    background: #161b22;
    border: none;
    padding: 1 3;
    margin: 1 0;
    width: 100%;
    color: #e6edf3;
}

.user-card .card-label {
    margin: 0 0 1 0;
}

.user-card .card-body {
    margin: 0;
}

.index-card {
    background: #161b22;
    border: none;
    padding: 1 3;
    margin: 1 0;
    width: 100%;
    color: #e6edf3;
}

.assistant-card {
    background: transparent;
    border: none;
    padding: 1 3;
    margin: 1 0;
    width: 100%;
    color: #e6edf3;
    min-height: 0;
}


.stats-bar {
    color: #484f58;
    padding: 0 3;
    margin: 0 0 1 0;
    width: 100%;
}

.system-msg {
    color: #8b949e;
    padding: 0 3;
    margin: 0;
    width: 100%;
    text-style: italic;
}

.card-label {
    height: 1;
    width: 100%;
    padding: 0;
    margin: 0 0 1 0;
    background: transparent;
}

.card-header {
    height: 1;
    width: 100%;
    text-align: center;
    content-align: center middle;
    padding: 0;
    margin: 0 0 1 0;
    background: transparent;
}

.card-loading {
    width: 100%;
    height: auto;
    background: transparent;
    margin: 0;
    min-height: 0;
}

.card-thinking {
    width: 100%;
    height: auto;
    background: transparent;
    margin: 0;
    min-height: 0;
}

.card-tools {
    width: 100%;
    height: auto;
    background: transparent;
    margin: 0;
}

.card-flow {
    width: 100%;
    height: auto;
    background: transparent;
    margin: 0;
    padding: 0;
    min-height: 0;
}

.card-body {
    height: auto;
    width: 100%;
    padding: 0;
    margin: 1 0;
    background: transparent;
}

.live-tool-indicator {
    height: 1;
    width: 100%;
    margin: 0 0 1 0;
    padding: 0;
    background: transparent;
}

.card-footer {
    height: 1;
    width: 100%;
    margin-top: 1;
    padding: 0;
    color: #8b949e;
}

/* A subagent window has nothing after the footer - the task dock is above it -
   so the last line of the report sat hard against the bottom edge. One line
   under it. Scoped to the subagent card on purpose: the lead agent's turns are
   separated by the next user message, so adding a line there would add a line
   between every pair of turns. */
.subagent-card .card-footer {
    margin-bottom: 1;
}


/* ═════════════════════════════════════════════════════════════
   3. BOTTOM DOCK & TODO PREVIEW BAR (Above Input Card)
   ═════════════════════════════════════════════════════════════ */
#bottom-dock {
    dock: bottom;
    height: auto;
    width: 100%;
    background: #000000;
    /* Left inset 2, right 0. The input runs to the window edge while the
       transcript keeps a 2-column gutter on the left, so the block you type
       into is measurably the widest thing on screen and lines up with the user
       card. Measured at 100 columns: input L=2 R=0, user card L=2 R=0. */
    padding: 0 0 0 2;
    margin: 0;
}


#todo-preview-bar {
    width: 100%;
    height: auto;
    max-height: 6;
    background: #161b22;
    border: none;
    border-bottom: solid #21262d;
    padding: 0 2;
    margin: 0;
    overflow-y: auto;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
    scrollbar-color: #30363d;
    scrollbar-background: #161b22;
    display: none;
    color: #8b949e;
}

#approval-bar {
    width: 100%;
    height: auto;
    background: #161b22;
    border: none;
    border-bottom: solid #21262d;
    padding: 0 1;
    margin: 0;
    display: none;
    align-vertical: middle;
}

#approval-info {
    width: 1fr;
    height: 1;
    overflow: hidden;
    content-align: left middle;
    padding: 0;
    color: #e6edf3;
}

#approval-info:hover {
    text-style: underline;
}

#approval-actions {
    width: 22;
    height: 1;
    align-vertical: middle;
}

.approval-btn {
    width: auto;
    margin-left: 1;
    min-width: 0;
    height: 1;
    border: none !important;
    padding: 0 1;
    content-align: center middle;
    text-style: bold;
}

#approval-btn-approve, .btn-approve {
    background: #238636;
    color: #ffffff;
}

#approval-btn-approve:hover, .btn-approve:hover {
    background: #2ea043;
}

#approval-btn-reject, .btn-reject {
    background: #b62324;
    color: #ffffff;
}

#approval-btn-reject:hover, .btn-reject:hover {
    background: #da3633;
}

#approval-diff-preview {
    width: 100%;
    height: auto;
    max-height: 12;
    min-height: 2;
    background: #0d1117;
    border: none;
    border-top: solid #21262d;
    padding: 0 2;
    margin: 0;
    display: none;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#input-card {
    height: auto;
    width: 100%;
    background: #161b22;
    border: none;
    padding: 0;
    margin: 0;
}

#cmd-hints, #hero-cmd-hints {
    height: auto;
    max-height: 6;
    background: #161b22;
    border: none;
    padding: 1 3 0 3;
    color: #e6edf3;
    display: none;
    scrollbar-size-vertical: 0;
}

HintRow, .cmd-hint {
    width: 100%;
    height: 1;
    padding: 0 1 0 1;
    margin-bottom: 0;
    color: #e6edf3;
}

HintRow.selected, .cmd-hint.selected {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#prompt-input, #prompt-input:focus, #prompt-input.-focus {
    background: #161b22;
    border: none;
    outline: none;
    /* One blank gray line under the text. It was 0, so the last line of what
       you type sat hard against the meta row below it. Height grows with it:
       1 over, 1 under, at least 1 for the text itself. */
    padding: 1 3 1 3;
    height: 3;
    min-height: 3;
    max-height: 8;
    color: #e6edf3;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#input-meta-bar {
    height: auto;
    padding: 0 3 1 3;
    background: #161b22;
    color: #8b949e;
}

#input-meta-left {
    width: auto;
    color: #58a6ff;
    margin-right: 2;
}

#input-meta-mode {
    width: auto;
    margin-right: 2;
}

#input-meta-thinking {
    width: auto;
    margin-right: 2;
}

#input-meta-spacer {
    width: 1fr;
}

/* The "more below" control that sits above the input when there is more
   conversation below. It lives directly under the bottom dock - on the black
   margin, not inside the gray input card - and collapses to no height at all
   when the text is empty, which is most of the time. An always-present blank
   line above the input costs a line forever to say nothing. */
#jump-bar {
    height: 1;
    background: #000000;
    padding: 0;
    margin: 0;
    display: none;
}

#jump-spacer {
    width: 1fr;
    height: 0;
}

#jump-to-new {
    width: auto;
    height: auto;
    color: #8b949e;
}

/* No underline on hover. The shared `.meta-clickable` underlines, which reads
   as a link; this is a jump control and it stays plain, brightening only. */
#jump-to-new:hover {
    color: #ffffff;
}

#input-meta-right {
    width: auto;
    color: #484f58;
    text-align: right;
}

.meta-clickable {
    color: #8b949e;
}

.meta-clickable:hover {
    color: #ffffff;
    text-style: underline;
}

/* ═════════════════════════════════════════════════════════════
   4. THINKING & TOOL CARDS
   ═════════════════════════════════════════════════════════════ */
.loading-block {
    width: 100%;
    height: auto;
    margin: 0 0 1 0;
}

.loading-header {
    width: 100%;
    height: 1;
    color: #8b949e;
}

.thinking-block {
    width: 100%;
    height: auto;
    margin: 0 0 1 0;
}

.thinking-header {
    width: 100%;
    height: 1;
    color: #8b949e;
}

.thinking-body {
    width: 100%;
    height: auto;
    padding: 1 2;
    /* One blank line between the header and the opened box. It was 0, so the
       box's top edge touched the `Thought for 14.4s` line and the two read as
       one block rather than a summary and its contents. */
    margin-top: 1;
    background: #0d1117;
    color: #8b949e;
    display: none;
}

.tool-block {
    width: 100%;
    height: auto;
    margin: 1 0;
}

.tool-header {
    width: 100%;
    height: 1;
    color: #8b949e;
}

.tool-details {
    width: 100%;
    height: auto;
    padding: 1 2;
    margin-top: 1;
    margin-bottom: 1;
    background: #0d1117;
    overflow-x: hidden;
    text-wrap: nowrap;
    text-overflow: clip;
    display: none;
}

/* ═════════════════════════════════════════════════════════════
   5. MODAL DIALOGS (76 Cols, #0d1117, Swift Theme)
   ═════════════════════════════════════════════════════════════ */
ModalScreen {
    align: center middle;
    background: rgba(0, 0, 0, 0.70);
    color: #e6edf3;
}

#modal-dialog {
    width: 76;
    max-width: 95%;
    height: auto;
    max-height: 85%;
    background: #0d1117;
    border: none;
    padding: 1 2;
}

#ask-modal-dialog {
    width: 78;
    max-width: 95%;
    height: auto;
    background: #161b22;
    border: none;
    padding: 1 2;
}

#ask-modal-dialog #ask-question {
    color: #ffffff;
    text-style: bold;
    margin: 1 0 1 0;
}

#ask-summary-content {
    width: 100%;
    height: auto;
    max-height: 12;
    overflow-y: auto;
    margin: 1 0;
    padding: 0;
    color: #e6edf3;
    display: none;
}

#ask-options {
    background: transparent;
    border: none !important;
    height: auto;
    max-height: 8;
    padding: 0;
    margin: 0 0 1 0;
}

#ask-options > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#ask-options > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#ask-custom-box {
    width: 100%;
    height: auto;
    margin: 1 0;
    display: none;
}

#ask-custom-label {
    color: #8b949e;
    margin: 0 0 1 0;
}

#ask-custom-input {
    background: #0d1117;
    border: round #58a6ff;
    height: 3;
    padding: 0 1;
    color: #e6edf3;
    margin: 0;
}

#modal-header {
    height: 1;
    margin: 0 0 1 0;
    width: 100%;
}

/* Content-sized scroll areas: without an explicit auto height the 1fr
   default stretches the dialog to max-height and leaves empty rows. */
#modal-scroll, #modal-body {
    height: auto;
    max-height: 60;
}

#modal-scroll {
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: transparent;
}

#file-picker-list {
    background: #0d1117;
    border: none !important;
    height: 1fr;
    max-height: 100%;
    padding: 0 1;
    margin: 0;
    overflow-x: hidden;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#file-picker-list > .option-list--option {
    background: transparent;
    color: #8b949e;
}

#file-picker-list > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: none;
}

#modal-title {
    width: 1fr;
    color: #e6edf3;
    text-style: bold;
}

#modal-esc {
    width: auto;
    color: #6e7681;
}

Input, #modal-dialog Input, #modal-search, #sp-name, #api-key-input, #model-name-input, #plan-new-input, #commit-input, #file-search-input {
    background: #161b22;
    border: none !important;
    outline: none !important;
    padding: 0 1;
    height: 1;
    min-height: 1;
    max-height: 1;
    margin: 0 0 1 0;
    color: #e6edf3;
}

Input:focus, Input.-focus, #modal-dialog Input:focus, #modal-dialog Input.-focus, #modal-search:focus, #sp-name:focus, #api-key-input:focus, #model-name-input:focus, #ask-custom-input:focus, #plan-new-input:focus, #commit-input:focus, #file-search-input:focus {
    background: #21262d;
    border: none !important;
    outline: none !important;
}

Input .input--placeholder {
    color: #6e7681;
}

Input .input--cursor {
    background: #58a6ff;
    color: #0d1117;
}

#modal-list {
    background: #0d1117;
    border: none !important;
    height: auto;
    max-height: 15;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-color-hover: #484f58;
    scrollbar-background: #0d1117;
    scrollbar-background-hover: #0d1117;
}

#modal-list:focus {
    border: none !important;
    background-tint: transparent 0%;
}

/* Changelog: the list fills the window, a release opens in a stacked window. */
#modal-dialog.releases-dialog {
    height: auto;
    max-height: 85%;
}

#modal-dialog.releases-dialog #modal-list {
    height: auto;
    max-height: 8;
}

#releases-preview {
    height: 1fr;
    max-height: 100%;
    background: #0d1117;
    border: none;
    border-top: solid #21262d;
    padding: 1 0 0 0;
    margin: 1 0 0 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#releases-body {
    width: 100%;
    height: auto;
    color: #e6edf3;
}

/* Sessions dialog: wide enough for title, relative time, token badge, model and dir */
#modal-dialog.sessions-dialog {
    width: 86;
    max-width: 95%;
    height: auto;
    max-height: 85%;
}

#modal-dialog.sessions-dialog #modal-list {
    height: auto;
    max-height: 16;
}

/* MCP servers: rows stay one line, prose goes in the line at the bottom. */
#modal-dialog.mcp-dialog {
    width: 96;
    height: auto;
    max-height: 80%;
}

#modal-dialog.mcp-dialog #modal-list {
    height: auto;
    /* Room for the servers and, in the options view, the six options.
       Without a cap the list grows and pushes the bottom line off. */
    max-height: 14;
    border: none;
    border-top: solid #21262d;
    margin: 0;
    padding: 0;
}

#mcp-empty {
    padding: 1 2;
    color: #6e7681;
    border: none;
    border-top: solid #21262d;
    height: auto;
}

#mcp-status {
    /* The one place in the window where prose appears. Sits at the bottom,
       above the key hints, and is the only scrolling region - the server
       list keeps its natural height so the rows do not jump around. */
    height: auto;
    max-height: 12;
    background: #0d1117;
    border: none;
    border-top: solid #21262d;
    padding: 0 1;
    margin: 0;
    color: #8b949e;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#mcp-footer {
    padding: 0 1;
    color: #6e7681;
    border: none;
    border-top: solid #21262d;
    height: auto;
}

/* Add server: a small form, same dialog chrome. */
#modal-dialog.mcp-add-dialog {
    width: 74;
    height: auto;
}

#mcp-add-form {
    height: auto;
    padding: 0 1;
}

/* Confirmation. Wider than the add form so a long server name does not
   wrap into an unreadable column.

   Its own class, not `#modal-dialog.mcp-add-dialog:has(#mcp-confirm-body)`.
   Textual has no :has() in CSS - it is a query-time pseudo-class there, not
   a selector - and one unknown pseudo-class raises a TokenError that takes
   the whole stylesheet down with it, blanking every screen in the app. */
#modal-dialog.mcp-confirm-dialog {
    width: 72;
}

#mcp-confirm-body {
    height: auto;
    padding: 1 1 0 1;
}

#mcp-confirm-text {
    height: auto;
    color: #c9d1d9;
}

#mcp-confirm-hint {
    height: auto;
    padding-top: 1;
    color: #6e7681;
}

#mcp-add-form .mcp-add-label {
    color: #6e7681;
    height: 1;
    margin-top: 1;
}

#mcp-add-form Input {
    margin: 0;
}

#f-type {
    height: 1;
    color: #c9d1d9;
}

#f-type:hover {
    color: #58a6ff;
}

#mcp-add-actions {
    height: 1;
    padding: 0 1;
    margin-top: 1;
}

#mcp-add-hint {
    color: #6e7681;
    width: 1fr;
}

#mcp-add-error {
    color: #f85149;
    width: 2fr;
    text-align: right;
}

/* Update: steps run in the window, esc only closes it. */
#modal-dialog.update-dialog {
    width: 76;
    height: auto;
    max-height: 80%;
}

#modal-dialog.update-dialog #update-step {
    width: 100%;
    height: 1;
    color: #d29922;
    margin: 1 0 0 0;
}

#update-log {
    width: 100%;
    height: auto;
    max-height: 10;
    overflow-y: auto;
    background: #0d1117;
    border: none;
    border-top: solid #21262d;
    padding: 1 0 0 0;
    margin: 1 0 0 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#update-log-body {
    width: 100%;
    height: auto;
    color: #e6edf3;
}

/* Agent tools: flat list like /help, click opens a stacked window. */
#modal-dialog.tools-dialog {
    width: 76;
    height: auto;
    max-height: 80%;
}

#modal-dialog.tool-note-dialog {
    width: 76;
    height: auto;
    max-height: 80%;
}

#tools-list {
    width: 100%;
    height: auto;
    max-height: 14;
    background: #0d1117;
    border: none !important;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#tools-list > .option-list--option {
    background: transparent;
    color: #8b949e;
}

#tools-list > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#releases-hint {
    height: 1;
    color: #8b949e;
    margin-bottom: 1;
}

#releases-preview {
    height: auto;
    max-height: 100%;
    background: #0d1117;
    border: none;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
    scrollbar-background: #0d1117;
}

#modal-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#modal-list > .option-list--option-hover {
    background: transparent !important;
    color: #8b949e !important;
}

#modal-list > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#modal-list > .option-list--option-selected {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#modal-footer {
    height: 1;
    margin-top: 1;
    width: 100%;
    color: #6e7681;
}

#modal-footer-left {
    width: 1fr;
    height: 1;
}

#modal-footer-right {
    width: auto;
    height: 1;
}

/* ═════════════════════════════════════════════════════════════
   6. SPLIT EDITOR-STYLE MODALS (Diff & Commit)
   ═════════════════════════════════════════════════════════════ */
#modal-dialog.diff-modal-dialog {
    width: 96%;
    max-width: 140;
    height: 85%;
    max-height: 90%;
    background: #0d1117;
    border: none;
    padding: 1 2;
}

#diff-split-container {
    width: 100%;
    height: 1fr;
    margin: 1 0;
}

#diff-files-sidebar {
    width: 38;
    height: 100%;
    background: #161b22;
    border-right: solid #30363d;
    padding: 0;
}

#diff-sidebar-title {
    background: #21262d;
    color: #8b949e;
    padding: 0 1;
    height: 1;
    text-style: bold;
}

#diff-file-list {
    background: #161b22;
    border: none !important;
    height: 1fr;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#diff-file-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#diff-file-list > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#diff-preview-scroll {
    width: 1fr;
    height: 100%;
    background: #0d1117;
    padding: 0 2;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#diff-preview-header {
    height: 1;
    background: #161b22;
    color: #e6edf3;
    padding: 0 1;
    margin-bottom: 1;
    text-style: bold;
}

#diff-content-body {
    width: 100%;
    height: auto;
}

/* Actions live in the footer row: one line high, buttons only. */
#diff-footer-actions {
    width: auto;
    height: 1;
    align-vertical: middle;
    margin: 0 2 0 0;
}

#diff-commit-btn {
    width: auto;
    height: 1;
    min-height: 1;
    background: #238636;
    color: #ffffff;
    border: none !important;
    padding: 0 2;
    text-style: bold;
}

#diff-commit-btn:hover {
    background: #2ea043;
}

#diff-commit-btn:focus {
    background: #2ea043;
    border: none !important;
}

#diff-reject-btn {
    width: auto;
    height: 1;
    min-height: 1;
    margin-left: 1;
    background: #b62324;
    color: #ffffff;
    border: none !important;
    padding: 0 2;
    text-style: bold;
}

#diff-reject-btn:hover {
    background: #da3633;
}

#diff-reject-btn:focus {
    background: #da3633;
    border: none !important;
}

#commit-box {
    height: 3;
    margin: 1 0 0 0;
    background: #161b22;
    padding: 0 1;
}

#commit-label {
    height: 1;
    color: #8b949e;
}

#commit-input {
    background: #0d1117;
    border: none;
    outline: none;
    height: 1;
    min-height: 1;
    max-height: 1;
    padding: 0 1;
    color: #e6edf3;
}

#commit-input:focus {
    background: #21262d;
    border: none;
    outline: none;
}

/* ═════════════════════════════════════════════════════════════
   7. PLAN MODAL (Max 6 visible tasks with scroll + 1 line gap)
   ═════════════════════════════════════════════════════════════ */
#modal-dialog.plan-modal-dialog {
    width: 76;
    max-width: 95%;
    height: auto;
    background: #0d1117;
    border: none;
    padding: 1 2;
}

#plan-list {
    background: #0d1117;
    border: none !important;
    height: auto;
    max-height: 6;
    padding: 0;
    margin: 0;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#plan-list:focus {
    border: none !important;
}

/* Textual tints the whole focused OptionList by 5%, which reads as "every row
   is selected". Only the highlighted row may carry a background. */
OptionList:focus {
    background-tint: transparent 0%;
}

#plan-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#plan-list > .option-list--option-highlighted {
    background: #1f6feb !important;
    color: #ffffff !important;
    text-style: bold;
}

#plan-new-input {
    background: #161b22;
    border: none;
    outline: none;
    padding: 0 1;
    height: 1;
    min-height: 1;
    max-height: 1;
    margin: 1 0 0 0;
    color: #e6edf3;
}

#plan-new-input:focus {
    background: #21262d;
    border: none;
    outline: none;
}

/* ═════════════════════════════════════════════════════════════
   8. CONTEXT INSPECTOR & WORKSPACE PICKER MODAL
   ═════════════════════════════════════════════════════════════ */
#modal-dialog.context-modal-dialog {
    width: 94%;
    max-width: 124;
    height: 84%;
    max-height: 88%;
    background: #0d1117;
    border: none;
    padding: 1 2;
}

#context-split-container {
    width: 100%;
    height: 1fr;
    margin: 1 0;
}

#context-info-sidebar {
    width: 48;
    height: 100%;
    background: #161b22;
    border-right: solid #30363d;
    padding: 0 1;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

#context-tree-container {
    width: 1fr;
    height: 100%;
    background: #0d1117;
    padding: 0 1;
}

#context-tree-header {
    height: 1;
    background: #161b22;
    color: #e6edf3;
    padding: 0 1;
    margin-bottom: 1;
    text-style: bold;
}

ContextDirectoryTree {
    background: #0d1117;
    height: 1fr;
    border: none;
    scrollbar-size-vertical: 1;
    scrollbar-color: #30363d;
}

ContextDirectoryTree:focus {
    border: none;
}
"""
