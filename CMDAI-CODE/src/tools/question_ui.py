from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import sys
import os
import json
import tempfile
import subprocess
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style


@dataclass
class Option:
    label: str
    description: str = ""


@dataclass
class Question:
    id: str
    question: str
    description: str = ""
    options: List[Option] = field(default_factory=list)


@dataclass
class AskUserState:
    questions: List[Question]
    mode: str = "answering"
    current_idx: int = 0
    cursor: int = 0
    answers: Dict[str, dict] = field(default_factory=dict)
    custom_inputs: Dict[str, str] = field(default_factory=dict)
    editing_custom: bool = False
    confirm_button: int = 1


def _dict_to_question(q: Any, idx: int) -> Question:
    if isinstance(q, Question):
        if not q.description:
            q.description = "Select your response."
        for o in q.options:
            if not o.description:
                o.description = f"Select {o.label}"
        return q
    if isinstance(q, str):
        return Question(id=str(idx), question=q, description="Select your response.")
    if isinstance(q, list):
        if len(q) > 0:
            return _dict_to_question(q[0], idx)
        return Question(id=str(idx), question=f"Question {idx+1}", description="Select your response.")
    if not isinstance(q, dict):
        return Question(id=str(idx), question=str(q), description="Select your response.")

    qid = str(q.get("id", q.get("question", q.get("title", q.get("text", q.get("name", str(idx)))))))
    qtext = q.get("question", q.get("title", q.get("text", q.get("prompt", q.get("name", q.get("header", q.get("label", "")))))))
    if not qtext:
        qtext = f"Question {idx+1}"
    qdesc = q.get("description", q.get("desc", q.get("details", q.get("info", ""))))
    if not qdesc:
        qdesc = f"Select the primary option for {qtext.lower()}"

    opts_raw = q.get("options", [])
    if isinstance(opts_raw, dict):
        opts_raw = list(opts_raw.values())
    elif not isinstance(opts_raw, list):
        opts_raw = [opts_raw]

    opts = []
    for o in opts_raw:
        if isinstance(o, Option):
            if not o.description:
                o.description = f"Select {o.label}"
            opts.append(o)
        elif isinstance(o, dict):
            lbl = str(o.get("label", o.get("text", o.get("name", o.get("option", "")))))
            desc = str(o.get("description", o.get("desc", "")))
            if not desc:
                desc = f"Select {lbl}"
            opts.append(Option(label=lbl, description=desc))
        else:
            lbl = str(o)
            opts.append(Option(label=lbl, description=f"Select {lbl}"))

    return Question(id=str(qid), question=str(qtext), description=str(qdesc), options=opts)


def render_tab_bar(state: AskUserState) -> list[tuple[str, str]]:
    parts = []
    for i, q in enumerate(state.questions):
        label = f"Question {i+1}"
        if state.mode == "answering" and i == state.current_idx:
            parts.append(("class:tab.active", f" [ {label} ] "))
        else:
            parts.append(("class:tab", f"   {label}   "))
    if state.mode == "reviewing":
        parts.append(("class:tab.active", " [ Confirm Answers ] "))
    else:
        parts.append(("class:tab", "   Confirm Answers   "))
    return parts


def render_question_content(state: AskUserState) -> list[tuple[str, str]]:
    q = state.questions[state.current_idx]
    lines = []
    lines.append(("class:question.text", f"  {q.question}\n"))
    if q.description:
        lines.append(("class:question.desc", f"  ↳ {q.description}\n\n"))
    else:
        lines.append(("", "\n"))

    for i, opt in enumerate(q.options):
        is_selected = (i == state.cursor)
        marker = "  ➔ " if is_selected else "    "
        style = "class:option.active" if is_selected else "class:option"
        lines.append((style, f"{marker}{opt.label}\n"))
        if opt.description:
            lines.append(("class:option.desc", f"    ↳ {opt.description}\n\n"))
        else:
            lines.append(("", "\n"))

    custom_idx = len(q.options)
    is_custom_selected = (state.cursor == custom_idx)
    marker = "  ➔ " if is_custom_selected else "    "
    style = "class:option.active" if is_custom_selected else "class:option"
    lines.append((style, f"{marker}Own answer:\n"))
    custom_text = state.custom_inputs.get(q.id, "")
    if state.editing_custom and is_custom_selected:
        lines.append(("class:option.input", f"    ↳ {custom_text}█\n"))
    elif custom_text:
        lines.append(("class:option.desc", f"    ↳ {custom_text}\n"))
    else:
        lines.append(("class:option.placeholder", "    ↳ Type your custom response here...\n"))

    return lines


def render_summary_content(state: AskUserState) -> list[tuple[str, str]]:
    lines = [("class:summary.header", "  Summary of your answers:\n\n")]
    for i, q in enumerate(state.questions):
        ans = state.answers.get(q.id)
        lines.append(("class:question.text", f"  {i+1}. {q.question}\n"))
        if ans is None:
            lines.append(("class:option.desc", "     ➔ (No answer)\n"))
        elif ans.get("type") == "option":
            opt_label = ans.get("value", "")
            opt = next((o for o in q.options if o.label == opt_label), None)
            lines.append(("class:option.active", f"     ➔ {opt_label}\n"))
            if opt and opt.description:
                lines.append(("class:option.desc", f"       ↳ {opt.description}\n"))
        else:
            lines.append(("class:option.active", f"     ➔ Own answer: {ans.get('value', '')}\n"))
        lines.append(("", "\n"))
    return lines


def render_footer(state: AskUserState) -> list[tuple[str, str]]:
    if state.mode == "answering":
        return [("class:footer",
            "  [Tab / ← / →] Switch Question    [↑ / ↓] Select Option    [Enter] Next Question    [Esc] Cancel")]
    else:
        return [("class:footer",
            "  [Enter] Submit    [← / → / Tab] Navigate")]


def render_buttons(state: AskUserState) -> list[tuple[str, str]]:
    if state.mode == "reviewing":
        btn_back = "[ Back to Edit ]"
        btn_submit = "[ Submit Answers ]"
        if state.confirm_button == 0:
            return [
                ("class:button.active", btn_back),
                ("", "  "),
                ("class:button", btn_submit),
                ("", " "),
            ]
        else:
            return [
                ("class:button", btn_back),
                ("", "  "),
                ("class:button.active", btn_submit),
                ("", " "),
            ]
    return [("", "")]


def _advance_question(state: AskUserState) -> None:
    if state.current_idx == len(state.questions) - 1:
        state.mode = "reviewing"
        state.confirm_button = 1
        state.cursor = 0
    else:
        state.current_idx += 1
        state.cursor = 0
        state.editing_custom = False


def _build_result(state: AskUserState) -> Dict[str, Any]:
    result = {}
    for q in state.questions:
        ans = state.answers.get(q.id)
        if ans is None:
            if q.options:
                opt = q.options[0]
                result[q.question] = {
                    "answer": opt.label,
                    "type": "option",
                    "description": opt.description
                }
            else:
                result[q.question] = {
                    "answer": "N/A",
                    "type": "option",
                    "description": ""
                }
        else:
            val = ans.get("value", "")
            ans_type = ans.get("type", "option")
            opt_desc = ""
            if ans_type == "option":
                opt = next((o for o in q.options if o.label == val), None)
                if opt and opt.description:
                    opt_desc = opt.description
            result[q.question] = {
                "answer": val,
                "type": ans_type,
                "description": opt_desc
            }
    return result


def run_question_wizard(questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    parsed = [_dict_to_question(q, i) for i, q in enumerate(questions)]
    state = AskUserState(questions=parsed)

    for q in parsed:
        if q.options:
            state.answers[q.id] = {"type": "option", "value": q.options[0].label}

    kb = KeyBindings()

    @kb.add("tab")
    @kb.add("right")
    def switch_next(event):
        if state.mode == "answering":
            state.current_idx = min(state.current_idx + 1, len(state.questions) - 1)
            state.cursor = 0
            state.editing_custom = False
        else:
            if state.confirm_button == 0:
                state.confirm_button = 1
            else:
                state.confirm_button = 0

    @kb.add("s-tab")
    @kb.add("left")
    def switch_prev(event):
        if state.mode == "answering" and state.current_idx > 0:
            state.current_idx -= 1
            state.cursor = 0
            state.editing_custom = False
        elif state.mode == "reviewing":
            if state.confirm_button == 1:
                state.confirm_button = 0
            else:
                state.confirm_button = 1

    @kb.add("up")
    def cursor_up(event):
        if state.mode == "answering" and not state.editing_custom:
            state.cursor = max(0, state.cursor - 1)

    @kb.add("down")
    def cursor_down(event):
        if state.mode == "answering" and not state.editing_custom:
            max_cursor = len(state.questions[state.current_idx].options)
            state.cursor = min(max_cursor, state.cursor + 1)

    @kb.add("enter")
    def confirm(event):
        if state.mode == "answering":
            q = state.questions[state.current_idx]
            if state.cursor == len(q.options):
                if state.editing_custom:
                    state.editing_custom = False
                    val = state.custom_inputs.get(q.id, "").strip()
                    if not val:
                        val = "N/A"
                    state.answers[q.id] = {"type": "custom", "value": val}
                    _advance_question(state)
                else:
                    state.editing_custom = True
            else:
                opt = q.options[state.cursor]
                state.answers[q.id] = {"type": "option", "value": opt.label}
                _advance_question(state)
        else:
            if state.confirm_button == 0:
                state.mode = "answering"
                state.current_idx = 0
                state.cursor = 0
                state.editing_custom = False
            else:
                event.app.exit(result=_build_result(state))

    @kb.add("c-c")
    def cancel_ctrl_c(event):
        event.app.exit(result=None)

    @kb.add("escape")
    def cancel(event):
        if state.editing_custom:
            state.editing_custom = False
            return
        if event.app.key_processor.empty:
            event.app.exit(result=None)

    for fkey in ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"]:
        @kb.add(fkey)
        def ignore_fkey(event):
            pass

    @kb.add("backspace")
    def backspace(event):
        if state.editing_custom:
            q = state.questions[state.current_idx]
            text = state.custom_inputs.get(q.id, "")
            state.custom_inputs[q.id] = text[:-1]

    @kb.add("space")
    def space_char(event):
        if state.editing_custom:
            q = state.questions[state.current_idx]
            state.custom_inputs[q.id] = state.custom_inputs.get(q.id, "") + " "

    @kb.add("<any>")
    def type_char(event):
        if state.editing_custom:
            q = state.questions[state.current_idx]
            char = event.data
            if char and len(char) == 1 and char.isprintable():
                state.custom_inputs[q.id] = state.custom_inputs.get(q.id, "") + char

    style = Style.from_dict({
        "frame.border": "#555555",
        "tab.active": "bold #ffffff bg:#005f87",
        "tab": "#888888",
        "question.text": "bold #ffffff",
        "question.desc": "#888888 italic",
        "option.active": "bold #00ffaa",
        "option": "#cccccc",
        "option.desc": "#888888",
        "option.placeholder": "#555555 italic",
        "option.input": "#ffffff bold",
        "footer": "#888888",
        "summary.header": "bold #ffffff",
        "button.active": "bold #ffffff bg:#005f87",
        "button": "#888888",
    })

    tab_bar_window = Window(
        height=1,
        content=FormattedTextControl(lambda: render_tab_bar(state))
    )
    divider = Window(height=1, char="─", style="class:frame.border")

    body_window = Window(
        content=FormattedTextControl(
            lambda: render_question_content(state) if state.mode == "answering"
                    else render_summary_content(state)
        )
    )

    footer_left = Window(
        height=1,
        content=FormattedTextControl(lambda: render_footer(state))
    )
    footer_right = Window(
        height=1,
        content=FormattedTextControl(lambda: render_buttons(state)),
        align="right",
    )
    footer_window = VSplit([footer_left, footer_right], height=1)

    top_border = VSplit([
        Window(char="╭", width=1, height=1, style="class:frame.border"),
        Window(char="─", height=1, style="class:frame.border"),
        Window(char="╮", width=1, height=1, style="class:frame.border"),
    ], height=1)
    bottom_border = VSplit([
        Window(char="╰", width=1, height=1, style="class:frame.border"),
        Window(char="─", height=1, style="class:frame.border"),
        Window(char="╯", width=1, height=1, style="class:frame.border"),
    ], height=1)

    inner = HSplit([
        tab_bar_window,
        divider,
        body_window,
        footer_window,
    ])

    layout = Layout(
        HSplit([
            top_border,
            VSplit([
                Window(char="│", width=1, style="class:frame.border"),
                inner,
                Window(char="│", width=1, style="class:frame.border"),
            ]),
            bottom_border,
        ])
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
    )

    result = app.run()
    return result if result is not None else {}


def execute_answer_tool(questions: Any = None, **kwargs) -> str:
    if not questions:
        for k in ["questions", "q", "question", "questions_list", "items", "list", "0", "args", "params", "parameters"]:
            if k in kwargs and kwargs[k]:
                questions = kwargs[k]
                break

    if isinstance(questions, str):
        try:
            parsed = json.loads(questions)
            if isinstance(parsed, (list, dict)):
                questions = parsed
            else:
                questions = [{"question": questions}]
        except Exception:
            questions = [{"question": questions}]

    if isinstance(questions, dict):
        if "questions" in questions:
            questions = questions["questions"]
        elif "question" in questions:
            questions = [questions]
        else:
            questions = list(questions.values())

    def _flatten(items):
        res = []
        if isinstance(items, list):
            for i in items:
                res.extend(_flatten(i))
        elif items is not None:
            res.append(items)
        return res

    questions = _flatten(questions)

    valid_questions = []
    for q in questions:
        if isinstance(q, str) and q.strip():
            valid_questions.append({"question": q.strip()})
        elif isinstance(q, dict) and (q.get("question") or q.get("title") or q.get("text") or q.get("id")):
            valid_questions.append(q)

    if not valid_questions:
        return "System Error: You called the 'answer' tool without providing any questions! Calling answer() with empty arguments {} is strictly FORBIDDEN. You MUST provide a non-empty 'questions' array containing at least one question object with 'question', 'description', and 'options' (each option with 'label' and 'description'). Please retry calling 'answer' now with your required questions."

    questions = valid_questions

    with tempfile.NamedTemporaryFile("w", delete=False, suffix="_q_in.json", encoding="utf-8") as f_in:
        json.dump(questions, f_in, ensure_ascii=False)
        in_path = f_in.name

    with tempfile.NamedTemporaryFile("w", delete=False, suffix="_q_out.json", encoding="utf-8") as f_out:
        out_path = f_out.name

    try:
        script_path = os.path.abspath(__file__)
        if os.name == "nt":
            cmd_str = f'cmd.exe /c start "CMDAI Answer Tool" /wait "{sys.executable}" "{script_path}" "{in_path}" "{out_path}"'
            subprocess.run(cmd_str, shell=True)
        else:
            subprocess.run([sys.executable, script_path, in_path, out_path])

        if os.path.exists(out_path):
            with open(out_path, "r", encoding="utf-8") as f:
                res_data = json.load(f)
        else:
            res_data = {}
    finally:
        for p in [in_path, out_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    if not res_data or "error" in res_data:
        return "User cancelled or closed the question window without submitting answers."

    formatted_lines = ["Summary of answers:"]
    for idx, (q_text, q_info) in enumerate(res_data.items(), start=1):
        if isinstance(q_info, dict):
            ans_val = q_info.get("answer", "")
            ans_type = q_info.get("type", "option")
            ans_desc = q_info.get("description", "")
        else:
            ans_val = str(q_info)
            ans_type = "option"
            ans_desc = ""

        formatted_lines.append(f"|_ {idx}. {q_text}")
        if ans_type == "custom":
            formatted_lines.append(f"   |_ ➔ Own answer: {ans_val}")
        else:
            formatted_lines.append(f"   |_ ➔ {ans_val}")
            if ans_desc:
                formatted_lines.append(f"      ↳ {ans_desc}")

    return "\n".join(formatted_lines)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        in_path = sys.argv[1]
        out_path = sys.argv[2]
        try:
            with open(in_path, "r", encoding="utf-8") as f:
                questions = json.load(f)
            res = run_question_wizard(questions)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception as e:
            import traceback
            traceback.print_exc()
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"error": str(e)}, f)
            print("\nWystąpił błąd podczas wyświetlania pytań.")
            input("Naciśnij Enter, aby zamknąć to okno...")
