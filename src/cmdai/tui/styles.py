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
    padding: 1 3 0 3;
    height: 2;
    min-height: 2;
    max-height: 8;
    color: #e6edf3;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#hero-meta-bar {
    height: auto;
    padding: 1 3 1 3;
    margin-top: 1;
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
    padding: 0 1 0 3;
    margin: 0;
    width: 100%;
    height: 1fr;
    /* Mouse wheel and keyboard scrolling stay enabled; the rail itself is
       intentionally hidden to keep the chat visually clean. */
    scrollbar-size-vertical: 0;
    scrollbar-gutter: auto;
}

.user-card {
    background: #161b22;
    border: none;
    padding: 1 3;
    margin: 1 0;
    width: 100%;
    color: #e6edf3;
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


/* ═════════════════════════════════════════════════════════════
   3. BOTTOM DOCK & TODO PREVIEW BAR (Above Input Card)
   ═════════════════════════════════════════════════════════════ */
#bottom-dock {
    dock: bottom;
    height: auto;
    width: 100%;
    background: #000000;
    padding: 1 1 0 3;
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
    background: #2c5380 !important;
    color: #ffffff !important;
    text-style: bold;
}

#prompt-input, #prompt-input:focus, #prompt-input.-focus {
    background: #161b22;
    border: none;
    outline: none;
    padding: 1 3 0 3;
    height: 2;
    min-height: 2;
    max-height: 8;
    color: #e6edf3;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#input-meta-bar {
    height: auto;
    padding: 1 3 1 3;
    margin-top: 1;
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
    margin-top: 0;
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
    background: #2c5380 !important;
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

#modal-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#modal-list > .option-list--option-hover {
    background: transparent !important;
    color: #8b949e !important;
}

#modal-list > .option-list--option-highlighted {
    background: #2c5380 !important;
    color: #ffffff !important;
    text-style: bold;
}

#modal-list > .option-list--option-selected {
    background: #2c5380 !important;
    color: #ffffff !important;
    text-style: bold;
}

#modal-footer {
    height: 1;
    margin-top: 1;
    width: 100%;
    color: #6e7681;
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
    background: #21262d !important;
    color: #58a6ff !important;
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

#diff-action-bar {
    height: 3;
    margin: 1 0 0 0;
    background: #161b22;
    padding: 0 1;
    align: right middle;
}

#diff-action-hint {
    width: 1fr;
    color: #8b949e;
    height: 1;
}

#diff-commit-btn {
    width: auto;
    height: 1;
    min-height: 1;
    background: #238636;
    color: #ffffff;
    border: none;
    padding: 0 2;
    text-style: bold;
}

#diff-commit-btn:hover {
    background: #2ea043;
}

#diff-commit-btn:focus {
    background: #2ea043;
    border: none;
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

#plan-list > .option-list--option {
    color: #8b949e;
    background: transparent;
}

#plan-list > .option-list--option-highlighted {
    background: #2c5380 !important;
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
