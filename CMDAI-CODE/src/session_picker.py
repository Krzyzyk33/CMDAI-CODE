import os
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style

def run_session_picker(sessions, current_session_id):
    options = ["[New session]"]
    for s in sessions:
        options.append(s)
    options.append("[Cancel]")

    selected_index = 0
    scroll_offset = 0
    delete_mode = False

    result = {"action": "cancel", "value": None}

    bindings = KeyBindings()

    @bindings.add("q")
    @bindings.add("c-c")
    def _(event):
        result["action"] = "cancel"
        event.app.exit()

    @bindings.add("up")
    def _(event):
        nonlocal selected_index, delete_mode
        selected_index = (selected_index - 1) % len(options)
        delete_mode = False

    @bindings.add("down")
    def _(event):
        nonlocal selected_index, delete_mode
        selected_index = (selected_index + 1) % len(options)
        delete_mode = False

    @bindings.add("right")
    def _(event):
        nonlocal delete_mode
        opt = options[selected_index]
        if isinstance(opt, dict):
            delete_mode = True

    @bindings.add("left")
    def _(event):
        nonlocal delete_mode
        delete_mode = False

    @bindings.add("enter")
    def _(event):
        opt = options[selected_index]
        if isinstance(opt, str):
            if opt == "[New session]":
                result["action"] = "new"
            else:
                result["action"] = "cancel"
        else:
            if delete_mode:
                result["action"] = "delete"
                result["value"] = opt["id"]
            else:
                result["action"] = "load"
                result["value"] = opt["id"]
        event.app.exit()

    def get_formatted_text():
        nonlocal scroll_offset
        import shutil
        term_height = shutil.get_terminal_size().lines
        visible_count = max(3, term_height - 5)

        if selected_index < scroll_offset:
            scroll_offset = selected_index
        elif selected_index >= scroll_offset + visible_count:
            scroll_offset = selected_index - visible_count + 1
        scroll_offset = max(0, min(scroll_offset, max(0, len(options) - visible_count)))

        lines = []
        lines.append(("", "\n"))
        lines.append(("class:active_tab", "  > Menadżer Sesji  \n\n"))

        visible_options = options[scroll_offset : scroll_offset + visible_count]

        if scroll_offset > 0:
            lines.append(("class:help", f"   ↑ ({scroll_offset} więcej wyżej)\n"))

        for local_i, opt in enumerate(visible_options):
            i = scroll_offset + local_i
            is_selected = (i == selected_index)

            if isinstance(opt, str):
                if is_selected:
                    lines.append(("class:selected", f" > {opt}\n"))
                else:
                    lines.append(("class:unselected", f"   {opt}\n"))
            else:
                s_id = opt["id"]
                s_date = opt["date"]
                active_mark = "*" if s_id == current_session_id else " "

                if is_selected and delete_mode:
                    lines.append(("class:delete_mode", f" > {active_mark} {s_id} ({s_date})  [Wciśnij ENTER, by usunąć]\n"))
                else:
                    delete_hint = "  (Naciśnij '→', by usunąć)" if is_selected else ""
                    if is_selected:
                        lines.append(("class:selected", f" > {active_mark} {s_id} ({s_date}){delete_hint}\n"))
                    else:
                        style = "class:active_item" if s_id == current_session_id else "class:unselected"
                        lines.append((style, f"   {active_mark} {s_id} ({s_date})\n"))

        remaining_below = len(options) - (scroll_offset + visible_count)
        if remaining_below > 0:
            lines.append(("class:help", f"   ↓ ({remaining_below} więcej niżej)\n"))

        lines.append(("class:help", "\n(Strzałki góra/dół: nawigacja | Strzałka w prawo: usuń | Enter: wybór | q: wyjście)\n"))
        return lines

    layout = Layout(
        HSplit([
            Window(content=FormattedTextControl(get_formatted_text))
        ])
    )

    style = Style.from_dict({
        "active_tab": "fg:#00ffff bold",
        "inactive_tab": "fg:#aaaaaa",
        "selected": "fg:#00ffff bold",
        "unselected": "fg:#aaaaaa",
        "active_item": "fg:#ffffff",
        "delete_mode": "fg:#ff0000 bold",
        "help": "fg:#666666 italic"
    })

    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True
    )
    app.run()
    return result
