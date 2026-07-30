class AppState:
    def __init__(self):
        self.is_generating = False
        self.stop_requested = False
        self.output_lines = []         # List of FormattedText/ANSI objects representing scrollback
        self.active_animation = None   # Current frame of an active animation, if any
        self.app = None                # Reference to the prompt_toolkit Application

# Global singleton
state = AppState()

from prompt_toolkit.formatted_text import ANSI

class StdoutProxy:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        if '\n' in self.buffer:
            lines = self.buffer.split('\n')
            for line in lines[:-1]:
                state.output_lines.append(ANSI(line.replace('\r', '')))
            self.buffer = lines[-1]
            if state.app:
                state.app.invalidate()

    def flush(self):
        if self.buffer:
            state.output_lines.append(ANSI(self.buffer.replace('\r', '')))
            self.buffer = ""
            if state.app:
                state.app.invalidate()
