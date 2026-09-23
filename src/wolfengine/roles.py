"""Board definitions: roles, abilities, consumables, night order, house rules.

Pure declarations. No logic lives here — `rules.py` reads these and decides. The point
of putting the house rules in a named dataclass is that each ambiguity in Chinese
Werewolf (同刀同救? 女巫自救? 被毒的猎人能不能开枪?) becomes a *field* with a value, so
which convention this engine follows is visible and testable rather than buried in an if.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Ability = Literal["kill", "wolf_chat", "check", "save", "poison", "shoot_on_death"]
# The three keys the win condition counts by. `rules.winner_for` is annotated with this, and
# `tests/test_wiring.py` reads it back out of here rather than keeping its own copy.
Team = Literal["wolf", "villager", "god"]


@dataclass(frozen=True)
class RoleSpec:
    id: str
    name_zh: str
    team: Team
    abilities: tuple[Ability, ...] = ()
    # Night order index; -1 = does not act at night. Wolves share one slot so the
    # engine can run their private channel before the kill is committed.
    night_slot: int = -1
    consumables: dict[str, int] = field(default_factory=dict)
    knows_teammates: bool = False


WOLF = RoleSpec("wolf", "狼人", "wolf", ("kill", "wolf_chat"), night_slot=1, knows_teammates=True)
VILLAGER = RoleSpec("villager", "平民", "villager", (), -1)
SEER = RoleSpec("seer", "预言家", "god", ("check",), night_slot=3)
WITCH = RoleSpec("witch", "女巫", "god", ("save", "poison"), night_slot=2,
                 consumables={"save": 1, "poison": 1})
HUNTER = RoleSpec("hunter", "猎人", "god", ("shoot_on_death",), -1)


@dataclass(frozen=True)
class HouseRules:
    """Every boundary case, resolved to one explicit choice.

    These are *not* neutral settings: they change who wins. They are in config_hash via
    board id, and rules.py must consult them instead of hard-coding a convention.
    """

    # 女巫用药
    self_save: Literal["never", "night1_only", "always"] = "night1_only"
    # 狼刀与女巫毒同一人时，解药救不活（毒优先于救）
    poison_overrides_save: bool = True
    # 女巫只能在知晓刀口的那晚用解药（不可留到以后救别的夜晚）
    save_only_same_night: bool = True
    # 双药不可同一晚都用（部分房规允许，这里不允许，减少结算组合数）
    one_potion_per_night: bool = True

    # 预言家
    check_killed_that_night: Literal["report", "unknown"] = "report"

    # 狼队
    wolf_self_kill: bool = True   # 允许自刀
    allow_empty_kill: bool = True  # 允许空刀（造成平安夜，是一种真战术）

    # 猎人
    hunter_shoots_on: frozenset[str] = frozenset({"wolf_kill", "exiled"})

    # 投票
    tie_break: Literal["pk_once_then_nobody", "nobody"] = "pk_once_then_nobody"

    # 遗言
    last_words_exiled: bool = True
    last_words_night_death_first_night: bool = True
    last_words_night_death_later: bool = False

    # 胜负：屠边（民全灭 或 神全灭 即狼胜），不是屠城
    win_condition: Literal["tu_bian", "tu_cheng"] = "tu_bian"


@dataclass(frozen=True)
class Board:
    id: str
    seat_count: int
    composition: tuple[tuple[RoleSpec, int], ...]
    night_order: tuple[str, ...]  # role ids, in the order they act
    house: HouseRules = HouseRules()

    @property
    def team_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r, n in self.composition:
            out[r.team] = out.get(r.team, 0) + n
        return out

    def spec(self, role_id: str) -> RoleSpec:
        for r, _ in self.composition:
            if r.id == role_id:
                return r
        raise KeyError(role_id)


BOARD_9 = Board(
    id="board9-v1",
    seat_count=9,
    composition=((WOLF, 3), (VILLAGER, 3), (SEER, 1), (WITCH, 1), (HUNTER, 1)),
    # 狼人先商定刀口 → 女巫见刀口用药 → 预言家最后验（这样"当晚被刀的人验出结果"
    # 这个边界在结算顺序上真的会发生，而不是被顺序掩盖掉）。
    night_order=("wolf", "witch", "seer"),
)

BOARDS = {BOARD_9.id: BOARD_9}


def board_for(seat_count: int = 9) -> Board:
    if seat_count != 9:
        # 只实现 9 人板。别的板要么改变阵营平衡（8 人 3 狼太强），要么需要新角色
        # （12 人加守卫/狼王，见 plan §13 判定为过度工程）。宁可报错不要悄悄换板。
        raise ValueError(f"only the 9-seat board exists; got {seat_count}")
    return BOARD_9
