from typing import Dict

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widgets import TextArea

from .problems import ERROR_BG, WARNING_BG

class DiagnosticTextArea(TextArea):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._markers: Dict[int, str] = {}

    @property
    def markers(self) -> Dict[int, str]:
        return dict(self._markers)

    def set_markers(self, markers: Dict[int, str]) -> None:

        if markers == self._markers:
            return
        self._markers = dict(markers)

        self.refresh()

    def clear_markers(self) -> None:
        self.set_markers({})

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        if not self._markers:
            return strip
        row = self.scroll_offset.y + y
        severity = self._markers.get(row)
        if severity is None:
            return strip

        selection = self.selection
        if selection is not None and selection.start != selection.end:

            first, last = sorted((selection.start[0], selection.end[0]))
            if first <= row <= last:
                return strip
        background = ERROR_BG if severity == "error" else WARNING_BG
        style = Style(bgcolor=background)
        segments = [
            Segment(
                segment.text,
                style if segment.style is None else segment.style + style,
            )
            for segment in strip
        ]
        return Strip(segments, strip.cell_length)
