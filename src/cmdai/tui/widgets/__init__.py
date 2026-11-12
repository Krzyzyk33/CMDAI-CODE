"""TUI widget components (re-exported for backwards compatibility)."""

from .helpers import (
    build_diff_text_from_details,
    copy_text_to_clipboard,
    get_lexer_for_path,
    is_api_error_message,
    subprocess_run_copy,
)
from .cards import (
    AssistantTurnCard,
    ClickableMessage,
    LiveToolIndicator,
    LoadingBlock,
    ThinkingBlock,
    TodoPreviewBar,
    ToolBlock,
    UserMessageCard,
)
from .input_box import (
    ApprovalBar,
    ApprovalDiffPreview,
    AutoExpandingInput,
    HintRow,
)
from .status_bars import (
    SessionNewTabBtn,
    SessionTabCloseBtn,
    SessionTabItem,
    SessionTabsBar,
    TimelineArrow,
    TimelineDot,
    TimelineRail,
)

__all__ = [
    "ApprovalBar",
    "ApprovalDiffPreview",
    "AssistantTurnCard",
    "AutoExpandingInput",
    "ClickableMessage",
    "HintRow",
    "LiveToolIndicator",
    "LoadingBlock",
    "SessionNewTabBtn",
    "SessionTabCloseBtn",
    "SessionTabItem",
    "SessionTabsBar",
    "ThinkingBlock",
    "TimelineArrow",
    "TimelineDot",
    "TimelineRail",
    "TodoPreviewBar",
    "ToolBlock",
    "UserMessageCard",
    "build_diff_text_from_details",
    "copy_text_to_clipboard",
    "get_lexer_for_path",
    "is_api_error_message",
    "subprocess_run_copy",
]
