from textual import events

class MouseActivatable:

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self.on_activate()

    def on_activate(self) -> None:

        raise NotImplementedError
