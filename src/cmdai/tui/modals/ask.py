from typing import Any, Dict, List, Optional, Union
from rich.text import Text
from textual import on, events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class AskModal(ModalScreen[Any]):

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select_or_submit", "Submit"),
        Binding("left", "nav_left", "Prev Question", show=False),
        Binding("right", "nav_right", "Next Question", show=False),
        Binding("1", "pick_1", "Pick 1", show=False),
        Binding("2", "pick_2", "Pick 2", show=False),
        Binding("3", "pick_3", "Pick 3", show=False),
        Binding("4", "pick_4", "Pick 4", show=False),
        Binding("5", "pick_5", "Pick 5", show=False),
    ]

    def __init__(
        self,
        question: Optional[str] = None,
        options: Optional[List[str]] = None,
        questions: Optional[List[Dict[str, Any]]] = None,
        start_in_summary: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if questions:
            self.questions: List[Dict[str, Any]] = questions[:5]
        elif question:
            self.questions = [{
                "question": question,
                "options": options or ["Yes, approve and continue", "No, revert changes", "Inspect diff again"],
            }]
        else:
            self.questions = [{
                "question": "Do you want to proceed with the proposed changes?",
                "options": ["Yes, approve and continue", "No, revert changes", "Inspect diff again"],
            }]

        self.current_idx: int = 0
        self.answers: Dict[int, str] = {}
        self.is_custom_mode: bool = False
        self.is_summary_mode: bool = start_in_summary

        self._load_current_question_data()

    def _load_current_question_data(self) -> None:
        curr = self.questions[self.current_idx]
        self.current_question_text = curr.get("question", "")
        base_opts = curr.get("options") or ["Yes, approve and continue", "No, revert changes", "Inspect diff again"]
        self.current_options = [
            o for o in base_opts
            if not o.lower().startswith("type custom") and not o.lower().startswith("write custom")
        ]
        self.custom_idx = len(self.current_options)
        self.all_choices = self.current_options + ["Type custom response..."]

    def _render_tabs_text(self) -> Text:
        t = Text()
        total = len(self.questions)
        for i in range(total):
            tab_label = f" Q{i + 1} "
            if not self.is_summary_mode and i == self.current_idx:
                t.append(tab_label, style="bold #ffffff on #1f6feb")
            elif i in self.answers:
                t.append(tab_label, style="bold #7ee787")
            else:
                t.append(tab_label, style="#8b949e")
            t.append("  ")

        if self.is_summary_mode:
            t.append(" Summary ", style="bold #ffffff on #1f6feb")
        else:
            t.append(" Summary ", style="#8b949e")
        return t

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-modal-dialog"):
            with Horizontal(id="modal-header"):
                yield Static(self._render_tabs_text(), id="modal-title")
                yield Static("[esc: cancel]", id="modal-esc")

            yield Static(f"[bold #ffffff]{self.current_question_text}[/]", id="ask-question")

            yield Static("", id="ask-summary-content")

            ol = OptionList(id="ask-options")
            for idx, opt in enumerate(self.all_choices):
                ol.add_option(Option(f"  {idx + 1}.  {opt}", id=f"opt_{idx}"))
            yield ol

            with Vertical(id="ask-custom-box"):
                yield Static("[bold #8b949e]Write your custom answer:[/]", id="ask-custom-label")
                yield Input(placeholder="Type response and press Enter...", id="ask-custom-input")

            with Horizontal(id="modal-footer"):
                max_key = len(self.all_choices)
                yield Static(
                    f"[1-{max_key}: Pick]  [←/→: Tab]  [enter: Confirm]  [esc: Cancel]",
                    id="ask-footer-keys",
                )

    def on_mount(self) -> None:
        if self.is_summary_mode:
            self._refresh_view()
        else:
            self.query_one("#ask-summary-content", Static).styles.display = "none"
            ol = self.query_one("#ask-options", OptionList)
            ol.highlighted = 0
            ol.focus()

    def _render_summary_content(self) -> Text:
        t = Text()
        for idx, q in enumerate(self.questions):
            q_text = q.get("question", "")
            ans = self.answers.get(idx, q.get("options", ["Approved"])[0])
            t.append(f"  {idx + 1}. {q_text}\n", style="bold #e6edf3")
            t.append(f"     ↳ {ans}\n\n", style="bold #7ee787")
        return t

    def _refresh_view(self) -> None:
        self.is_custom_mode = False
        self.query_one("#modal-title", Static).update(self._render_tabs_text())
        summary_widget = self.query_one("#ask-summary-content", Static)
        ol = self.query_one("#ask-options", OptionList)
        custom_box = self.query_one("#ask-custom-box", Vertical)
        custom_box.styles.display = "none"

        if self.is_summary_mode:
            self.query_one("#ask-question", Static).update("[bold #58a6ff]Confirmation & Summary[/]")
            summary_widget.update(self._render_summary_content())
            summary_widget.styles.display = "block"

            ol.clear_options()
            ol.add_option(Option("  1.  Start execution", id="opt_start"))
            ol.add_option(Option("  2.  Go back & edit answers", id="opt_back"))
            ol.highlighted = 0
            ol.styles.display = "block"
            ol.focus()

            self.query_one("#ask-footer-keys", Static).update(
                "[1: Start]  [2: Edit]  [←: Prev Tab]  [enter: Confirm]  [esc: Cancel]"
            )
        else:
            summary_widget.styles.display = "none"
            self._load_current_question_data()
            self.query_one("#ask-question", Static).update(f"[bold #ffffff]{self.current_question_text}[/]")

            ol.clear_options()
            for idx, opt in enumerate(self.all_choices):
                ol.add_option(Option(f"  {idx + 1}.  {opt}", id=f"opt_{idx}"))

            existing_ans = self.answers.get(self.current_idx)
            sel_idx = 0
            if existing_ans and existing_ans in self.current_options:
                sel_idx = self.current_options.index(existing_ans)
            ol.highlighted = sel_idx
            ol.styles.display = "block"
            ol.focus()

            max_key = len(self.all_choices)
            self.query_one("#ask-footer-keys", Static).update(
                f"[1-{max_key}: Pick]  [←/→: Tab]  [enter: Confirm]  [esc: Cancel]"
            )

    def _submit_answer(self, ans: str) -> None:
        self.answers[self.current_idx] = ans
        if self.current_idx + 1 < len(self.questions):
            self.current_idx += 1
            self._refresh_view()
        else:
            self.is_summary_mode = True
            self._refresh_view()

    def _finish_and_dismiss(self) -> None:
        result_list = []
        for idx, q in enumerate(self.questions):
            ans = self.answers.get(idx, q.get("options", ["Approved"])[0])
            result_list.append({"question": q.get("question", ""), "answer": ans})

        if len(self.questions) == 1:
            self.dismiss(result_list[0]["answer"])
        else:
            self.dismiss(result_list)

    def _pick_index(self, idx: int) -> None:
        if self.is_summary_mode:
            if idx == 0:
                self._finish_and_dismiss()
            else:
                self.is_summary_mode = False
                self.current_idx = len(self.questions) - 1
                self._refresh_view()
            return

        if idx == self.custom_idx:
            self._activate_custom_input()
        elif 0 <= idx < len(self.current_options):
            self._submit_answer(self.current_options[idx])

    def _activate_custom_input(self) -> None:
        self.is_custom_mode = True
        self.query_one("#ask-options", OptionList).styles.display = "none"
        custom_box = self.query_one("#ask-custom-box", Vertical)
        custom_box.styles.display = "block"
        custom_input = self.query_one("#ask-custom-input", Input)
        custom_input.value = ""
        custom_input.focus()
        self.query_one("#ask-footer-keys", Static).update("[enter: Submit answer]  [esc: Back to options]")

    def action_nav_left(self) -> None:
        if self.is_custom_mode:
            return
        if self.is_summary_mode:
            self.is_summary_mode = False
            self.current_idx = len(self.questions) - 1
            self._refresh_view()
        elif self.current_idx > 0:
            self.current_idx -= 1
            self._refresh_view()

    def action_nav_right(self) -> None:
        if self.is_custom_mode:
            return
        if not self.is_summary_mode:
            if self.current_idx + 1 < len(self.questions):
                self.current_idx += 1
                self._refresh_view()
            else:
                self.is_summary_mode = True
                self._refresh_view()

    def action_pick_1(self) -> None:
        self._pick_index(0)

    def action_pick_2(self) -> None:
        self._pick_index(1)

    def action_pick_3(self) -> None:
        self._pick_index(2)

    def action_pick_4(self) -> None:
        self._pick_index(3)

    def action_pick_5(self) -> None:
        self._pick_index(4)

    def action_cancel(self) -> None:
        if self.is_custom_mode:
            self.is_custom_mode = False
            self.query_one("#ask-custom-box", Vertical).styles.display = "none"
            ol = self.query_one("#ask-options", OptionList)
            ol.styles.display = "block"
            ol.focus()
            max_key = len(self.all_choices)
            self.query_one("#ask-footer-keys", Static).update(
                f"[1-{max_key}: Pick]  [←/→: Tab]  [enter: Confirm]  [esc: Cancel]"
            )
            return

        if self.is_summary_mode:
            self.is_summary_mode = False
            self.current_idx = len(self.questions) - 1
            self._refresh_view()
            return

        if self.current_idx > 0:
            self.current_idx -= 1
            self._refresh_view()
            return

        self.dismiss("" if len(self.questions) == 1 else [])

    def action_select_or_submit(self) -> None:
        if self.is_custom_mode:
            custom_input = self.query_one("#ask-custom-input", Input)
            val = custom_input.value.strip()
            self._submit_answer(val if val else "Custom response")
            return

        ol = self.query_one("#ask-options", OptionList)
        if ol.highlighted is not None:
            self._pick_index(ol.highlighted)
        else:
            if self.is_summary_mode:
                self._finish_and_dismiss()
            else:
                self.dismiss("" if len(self.questions) == 1 else [])

    @on(OptionList.OptionSelected)
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._pick_index(event.option_index)

    @on(Input.Submitted, "#ask-custom-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self._submit_answer(val if val else "Custom response")

