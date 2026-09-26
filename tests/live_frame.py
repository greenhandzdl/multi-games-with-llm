"""测试专用的渲染助手：把 `render_live.draw` 画到一段纯文本里，好在断言里数字符串。

`#155` 把它从 `src/wolfengine/render_live.py` 搬到这里。搬的原因不是它写坏了，是它**没有生产读者**：
`watch()` 自己建 `Console` 调 `draw`，全仓库点它名的三十七处都在测试里。住在产品代码里的后果是
那条"树上挂着没人拿的扳手"的闸门（`#81`）替它付了账——它的读者名册里本来就有 `tests/`。

`soft_wrap=True` 这一格是它存在的唯一理由，也是它必须留在测试侧的理由：**一行都不许被折断**，否则
每一条"这句话不在屏幕上"的断言都会因为文字被切成两行而假绿。产品那一侧要的是终端宽度下的排版，
不需要这个性质。
"""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console

from wolfengine.events import Event
from wolfengine.render_live import draw


def frame_text(events: list[Event], meta: dict[str, Any], *, god: bool = False,
               reveal_seat: int | None = None, width: int = 100) -> str:
    """The frame as plain text — see the module docstring for why `soft_wrap` is the point."""
    buf = StringIO()
    draw(Console(file=buf, soft_wrap=True, width=width, color_system=None,
                 force_terminal=False, highlight=False),
         events, meta, god=god, reveal_seat=reveal_seat)
    return buf.getvalue()
