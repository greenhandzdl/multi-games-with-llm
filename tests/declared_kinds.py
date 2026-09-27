"""事件的**声明**词表，推导一次给全表扫描用（`#160` 把它从 `events.py` 搬进来）。

产品侧不读它：发射时的守门人是 `compress.render_line` 那条未渲染分支，不是这份名单。
它的读者只有两条整表扫描的用例，所以按 `#155` 给 `frame_text` 定下的那条走——读者只在测试侧
的东西就住在测试侧。

推导只写这一遍，判定也只住一处：`test_wiring.py` 的 `_undeclared_kinds()`。`#153` 记过那一笔账——
第一版把判定写在守卫体内，对照用例于是自己另抄了一遍，结果"把判定改瞎"那具变异活了下来
（两遍实现里瞎掉的那遍没人看）。
"""
from __future__ import annotations

from wolfengine.events import Kind

KINDS: frozenset[str] = frozenset(
    v for k, v in vars(Kind).items() if not k.startswith("_") and isinstance(v, str)
)
