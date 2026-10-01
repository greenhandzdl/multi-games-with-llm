"""Table-driven coverage of the rules core: zero LLM, zero I/O, runs on every commit.

The night settlement is where a hand-written werewolf engine usually lies: 刀/救/毒 is a
three-way interaction with five reachable outcomes, and each house-rule boundary
(女巫自救? 同刀同毒算谁杀的? 被毒的猎人能不能开枪?) changes who wins. Every row here
names one of those boundaries, so a future refactor that silently flips a convention
fails here rather than in a 6-hour batch.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from wolfengine import persona
from wolfengine.config import Config
from wolfengine.roles import BOARD_9, Board, HouseRules, SEER, VILLAGER, WOLF, board_for
from wolfengine.rules import (
    NightPlan,
    alive_team_counts,
    check_win,
    deal,
    legal_actions,
    needs_last_words,
    resolve_night,
    resolve_tie,
    apply_night,
    tally_votes,
    winner_for,
)
from wolfengine.state import Death, GameState, Phase, SeatState


def mk_state(roles: dict[int, str], day: int = 1, house: HouseRules | None = None,
             dead: tuple[int, ...] = ()) -> GameState:
    board = BOARD_9 if house is None else Board9With(house)
    st = GameState(
        board=board,
        game_id="test",
        deal_seed=1,
        seats={s: SeatState(seat=s, role=r, alive=s not in dead) for s, r in roles.items()},
        day=day,
    )
    for seat, role in roles.items():
        if role == "witch":
            st.witch_seat = seat
        elif role == "seer":
            st.seer_seat = seat
        elif role == "hunter":
            st.hunter_seat = seat
    return st


# 1=wolf 2=seer 3=witch 4=vill 5=vill 6=hunter 7=wolf 8=vill 9=wolf
BASE = {1: "wolf", 2: "seer", 3: "witch", 4: "villager", 5: "villager",
        6: "hunter", 7: "wolf", 8: "villager", 9: "wolf"}


# ------------------------------------------------------------------ deal / board


def test_board_pool_sums_to_seat_count():
    assert sum(n for _, n in BOARD_9.composition) == BOARD_9.seat_count
    assert BOARD_9.team_counts == {"wolf": 3, "villager": 3, "god": 3}


def test_board_for_rejects_other_seat_counts():
    with pytest.raises(ValueError):
        board_for(8)


def test_a_board_whose_pool_does_not_fill_its_seats_cannot_be_built():
    """Both directions: `rules.deal` pairs the pool to the seats with `zip`, so a pool one short
    silently leaves a seat undealt and a pool one long silently leaves a role nobody holds."""
    with pytest.raises(ValueError, match="pools") as caught:
        Board(id="bad8", seat_count=8, composition=BOARD_9.composition)
    assert "8" in str(caught.value) and "9" in str(caught.value), \
        f"拒的是哪一格要对得上数：{caught.value}"
    with pytest.raises(ValueError, match="pools"):
        Board(id="bad10", seat_count=10, composition=BOARD_9.composition)


def test_a_smaller_consistent_board_still_deals():
    """The other side of that guard: it compares counts, so nothing here is 9-seat specific."""
    board = Board(id="tiny3", seat_count=3,
                  composition=((WOLF, 1), (VILLAGER, 1), (SEER, 1)))
    assert set(deal(board, random.Random(1))) == {1, 2, 3}


def test_deal_is_seed_deterministic_and_role_accurate():
    for _ in range(20):
        seed = random.randrange(10_000)
        a, b = deal(BOARD_9, random.Random(seed)), deal(BOARD_9, random.Random(seed))
        assert a == b, "same deal_seed must give the same table"
        assert Counter(a.values()) == Counter(
            r.id for r, n in BOARD_9.composition for _ in range(n)
        )
        assert set(a) == set(range(1, 10))


# ------------------------------------------------------------------ night resolution


class TestNight:
    def test_knife_without_save_kills(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=5))
        assert res.dead_seats == [5] and res.deaths[0].cause == "wolf_kill" and not res.peace

    def test_knife_solved_by_save_is_peace(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=5, witch_save=True))
        assert res.deaths == [] and res.used_save and res.peace

    def test_empty_knife_is_peace(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=None))
        assert res.peace and res.dead_seats == []

    def test_knife_and_poison_on_different_seats_double_death(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=5, witch_poison=1))
        assert res.dead_seats == [1, 5] and res.used_poison
        assert {d.cause for d in res.deaths} == {"wolf_kill", "poison"}
        # deaths ordered by seat so the log is deterministic given the same plan
        assert [d.seat for d in res.deaths] == sorted(res.dead_seats)

    def test_poison_on_the_knife_target_owns_the_death(self):
        """同刀同毒: cause decides whether the hunter may shoot, so attribution matters."""
        st = mk_state(BASE)
        res = resolve_night(st, NightPlan(wolf_kill=6, witch_poison=6))  # 6 = hunter
        assert res.dead_seats == [6]
        assert res.deaths[0].cause == "poison", "wolf_kill here would wrongly grant a shot"

    def test_self_save_allowed_night_one_only(self):
        h_on = resolve_night(mk_state(BASE, day=1), NightPlan(wolf_kill=3, witch_save=True))
        assert h_on.deaths == [] and h_on.used_save
        h_off = resolve_night(mk_state(BASE, day=2), NightPlan(wolf_kill=3, witch_save=True))
        assert h_off.dead_seats == [3]
        assert "save_blocked_self_save_night1_only" in h_off.blocked[0]

    def test_self_save_never_house_rule(self):
        res = resolve_night(mk_state(BASE, day=1, house=HouseRules(self_save="never")),
                           NightPlan(wolf_kill=3, witch_save=True))
        assert res.dead_seats == [3] and not res.used_save

    def test_save_without_a_knife_is_blocked(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=None, witch_save=True))
        assert "save_without_kill" in res.blocked[0] and res.deaths == []

    def test_out_of_potion_is_blocked_and_reported(self):
        st = mk_state(BASE)
        st.witch.save_left = 0
        res = resolve_night(st, NightPlan(wolf_kill=5, witch_save=True))
        assert res.dead_seats == [5] and "save_no_potion" in res.blocked[0]

    def test_two_potions_one_night_drops_the_poison_not_silently(self):
        res = resolve_night(mk_state(BASE), NightPlan(wolf_kill=5, witch_save=True, witch_poison=1))
        assert res.dead_seats == [] and res.used_save and not res.used_poison
        assert any("dropped_poison" in b for b in res.blocked)

    def test_two_potions_permitted_house_rule_variant(self):
        res = resolve_night(mk_state(BASE, house=HouseRules(one_potion_per_night=False)),
                           NightPlan(wolf_kill=5, witch_save=True, witch_poison=1))
        assert res.dead_seats == [1] and res.used_save and res.used_poison

    def test_kill_target_of_dead_seat_is_rejected_not_double_counted(self):
        st = mk_state(BASE, dead=(5,))
        res = resolve_night(st, NightPlan(wolf_kill=5))
        assert "invalid_wolf_kill_target:5" in res.blocked and res.deaths == []

    def test_poison_target_of_dead_seat_is_rejected_and_the_night_stays_peaceful(self):
        """毒那一侧的兜底和刀那一侧不是同一条代码，所以要单独钉。

        钉的不只是"blocked 里多了一句话"，是那句话之后这一晚的形状：没有死亡、`used_poison`
        不成立、`peace` 成立。少了后半截，一次幻觉目标在日志里会读成"女巫下手了但没人死"。
        """
        st = mk_state(BASE, dead=(5,))
        res = resolve_night(st, NightPlan(witch_poison=5))
        assert "invalid_poison_target:5" in res.blocked
        assert res.deaths == [] and not res.used_poison and res.peace

    def test_same_knife_same_poison_belongs_to_the_poison(self):
        """同刀同毒算谁杀的：房规允许双药同晚时，救不活那一口人（毒优先）。

        这一格决定猎人开不开枪——`hunter_shoots_on` 认的是死因，把 "poison" 覆写成
        "wolf_kill" 就等于替狼队多送一次枪。所以三条都断：blocked 里那句、死因是 poison、
        以及 used_save 不成立而 used_poison 成立。
        """
        st = mk_state(BASE, house=HouseRules(one_potion_per_night=False))
        res = resolve_night(st, NightPlan(wolf_kill=5, witch_save=True, witch_poison=5))
        assert "poison_overrides_save" in res.blocked
        assert res.dead_seats == [5] and res.deaths[0].cause == "poison"
        assert not res.used_save and res.used_poison


class TestSeerCheck:
    def test_reports_team(self):
        st = mk_state(BASE)
        assert resolve_night(st, NightPlan(seer_check=1)).seer_report == (1, "wolf")
        assert resolve_night(st, NightPlan(seer_check=4)).seer_report == (4, "good")

    def test_killed_that_night_still_reports_under_default_rule(self):
        st = mk_state(BASE)
        res = resolve_night(st, NightPlan(wolf_kill=4, seer_check=4))
        assert res.seer_report == (4, "good")

    def test_unknown_variant(self):
        st = mk_state(BASE, house=HouseRules(check_killed_that_night="unknown"))
        res = resolve_night(st, NightPlan(wolf_kill=4, seer_check=4))
        assert res.seer_report == (4, "unknown")

    def test_cannot_check_self_or_repeat(self):
        st = mk_state(BASE)
        assert any("invalid_check_target" in b for b in resolve_night(st, NightPlan(seer_check=2)).blocked)
        st.seer_results = {4: "good"}
        assert any("already_checked:4" in b for b in resolve_night(st, NightPlan(seer_check=4)).blocked)


# ------------------------------------------------------------------------ applying


def test_apply_night_consumes_potions_and_records_deaths():
    st = mk_state(BASE)
    apply_night(st, resolve_night(st, NightPlan(wolf_kill=5, witch_save=False, witch_poison=1)))
    assert st.witch.poison_left == 0 and st.witch.save_left == 1
    assert not st.is_alive(1) and not st.is_alive(5)
    assert len(st.deaths) == 2


def test_hunter_shot_pending_only_for_legal_causes():
    killed_by_wolves = mk_state(BASE)
    apply_night(killed_by_wolves, resolve_night(killed_by_wolves, NightPlan(wolf_kill=6)))
    assert killed_by_wolves.pending_shots == [6]

    poisoned = mk_state(BASE)
    apply_night(poisoned, resolve_night(poisoned, NightPlan(wolf_kill=5, witch_poison=6)))
    assert poisoned.pending_shots == [], "被毒死的猎人不开枪"


def test_seer_result_is_mirrored_into_state():
    st = mk_state(BASE)
    apply_night(st, resolve_night(st, NightPlan(seer_check=7)))
    assert st.seer_results == {7: "wolf"}


# ------------------------------------------------------------------- win conditions


@pytest.mark.parametrize(
    "alive,expected",
    [
        ({"wolf": 0, "villager": 3, "god": 3}, "good"),
        ({"wolf": 0, "villager": 0, "god": 0}, "good"),   # wolves dead = good wins, period
        ({"wolf": 3, "villager": 0, "god": 3}, "wolf"),   # 屠边: 民全灭
        ({"wolf": 3, "villager": 3, "god": 0}, "wolf"),   # 屠边: 神全灭
        ({"wolf": 1, "villager": 0, "god": 1}, "wolf"),
        ({"wolf": 1, "villager": 1, "god": 1}, None),
        ({"wolf": 2, "villager": 3, "god": 2}, None),
    ],
)
def test_tu_bian_win_table(alive, expected):
    assert winner_for(BOARD_9, Counter(alive)) == expected


@pytest.mark.parametrize(
    "alive,expected",
    [({"wolf": 2, "villager": 0, "god": 3}, None),      # 屠城 needs every good dead
     ({"wolf": 2, "villager": 0, "god": 0}, "wolf")],
)
def test_tu_cheng_is_a_different_game(alive, expected):
    board = Board9With(HouseRules(win_condition="tu_cheng"))
    assert winner_for(board, Counter(alive)) == expected


def Board9With(house: HouseRules):
    import dataclasses

    return dataclasses.replace(BOARD_9, house=house)


def test_check_win_is_idempotent_and_stops_escalation():
    st = mk_state(BASE)
    for s in (4, 5, 8):  # all villagers dead
        st.seats[s].alive = False
    assert check_win(st) == "wolf"
    st.seats[2].alive = False  # gods wiped too, but the game already called
    assert check_win(st) == "wolf"


def test_a_role_keyed_counter_ends_the_game_before_it_starts():
    """#80 的后果那一格：把**职业**版计数喂进胜负判据，九口人一个没死也算狼赢。

    断言的不是"应该如此"，是"这就是骗一次接线的代价"：`role_of` 的取值里没有 "god"，屠边那条
    `get("god", 0) == 0` 于是恒真。`tests/test_wiring.py` 钉住名字/注解/孪生函数时引用的读数
    就是这里——判据本身今天是对的，所以红不在这一格。
    """
    st = mk_state(BASE)
    by_role = Counter(st.role_of(s) for s in st.alive_seats)
    assert len(by_role) == 5, f"这张桌的职业键不是五种，下面两行是在自证：{by_role}"
    assert "god" not in by_role and "villager" in by_role
    assert winner_for(st.board, by_role) == "wolf", "按职业建键时，开局即终局这条路断了"
    assert winner_for(st.board, alive_team_counts(st)) is None


# ----------------------------------------------------------------------- voting


class TestVote:
    def test_simple_majority(self):
        r = tally_votes({1: 5, 2: 5, 3: 4, 4: 4, 5: 6}, eligible=list(range(1, 10)))
        assert r.out == 5 and not r.tied and r.tally[5] == 2

    def test_two_way_tie_gives_pk_seats_and_no_exile(self):
        r = tally_votes({1: 5, 2: 5, 3: 4, 7: 4}, eligible=list(range(1, 10)))
        assert r.out is None and r.tied and r.pk_seats == (4, 5)

    def test_three_way_tie(self):
        r = tally_votes({1: 4, 2: 5, 3: 6}, eligible=list(range(1, 10)))
        assert r.tied and r.pk_seats == (4, 5, 6) and r.out is None

    def test_everyone_abstains(self):
        r = tally_votes({1: None, 2: None}, eligible=[1, 2, 3])
        assert r.out is None and r.tally == {} and r.abstainers == [1, 2]

    def test_vote_for_dead_seat_or_self_cannot_exile_anyone(self):
        """Backstop for a legality-gate bug: a phantom vote must never create a death."""
        r = tally_votes({1: 9, 2: 2}, eligible=[1, 2, 3, 4, 5])  # 9 dead, 2 self-vote
        assert r.tally == {} and r.out is None and sorted(r.abstainers) == [1, 2]

    def test_pk_second_tie_exiles_nobody(self):
        second = tally_votes({1: 4, 2: 5, 3: 3}, eligible=[4, 5])   # 复投还是那个平票
        st = mk_state(BASE)
        assert resolve_tie(st, second) is None

    def test_pk_second_round_can_decide(self):
        st = mk_state(BASE)
        second = tally_votes({1: 4, 2: 4, 3: 5}, eligible=[4, 5])   # 复投改出了多数
        assert resolve_tie(st, second) == 4

    def test_a_nobody_house_ends_the_tie_even_when_the_revote_decides(self):
        """`tie_break="nobody"` 一条都不放逐，哪怕复投已经改出了多数。

        默认那一支（`pk_once_then_nobody`）上面两条已经钉住了；这一支是家规的另一半：
        房东说平票就是平票，引擎不能拿"复投有结果了"当理由偷偷放逐一个人。
        """
        st = mk_state(BASE, house=HouseRules(tie_break="nobody"))
        second = tally_votes({1: 4, 2: 4, 3: 5}, eligible=[4, 5])   # 和上一条同票面
        assert second.out == 4  # 夹具本身有胜者，None 只能来自那一支
        assert resolve_tie(st, second) is None


# ------------------------------------------------------------------- legal actions


class TestLegalActions:
    def test_wolf_may_not_kill_self_but_may_kill_teammate_by_default(self):
        st = mk_state(BASE)
        st.phase = Phase.NIGHT_WOLF
        la = legal_actions(st, 1)
        assert 1 not in la.targets and 7 in la.targets and la.allow_pass
        no_selfkill = mk_state(BASE, house=HouseRules(wolf_self_kill=False))
        no_selfkill.phase = Phase.NIGHT_WOLF
        assert 7 not in legal_actions(no_selfkill, 1).targets

    def test_seer_cannot_check_self_or_checked_or_dead(self):
        st = mk_state(BASE)
        st.phase = Phase.NIGHT_SEER
        st.seer_results = {4: "good"}
        la = legal_actions(st, 2)
        assert 2 not in la.targets and 4 not in la.targets
        assert 1 in la.targets

    def test_dead_seat_has_no_actions(self):
        st = mk_state(BASE, dead=(3,))
        st.phase = Phase.NIGHT_WITCH
        assert legal_actions(st, 3).acts == () and legal_actions(st, 3).reason_if_empty == "dead"

    def test_witch_options_shrink_with_consumables(self):
        st = mk_state(BASE)
        st.phase = Phase.NIGHT_WITCH
        assert set(legal_actions(st, 3).consumables) == {"save", "poison"}
        st.witch.save_left = 0
        assert legal_actions(st, 3).consumables == ("poison",)
        st.witch.poison_left = 0
        la = legal_actions(st, 3)
        assert la.acts == ("pass",) and la.consumables == ()

    def test_dead_hunter_may_shoot_but_other_dead_seats_may_not(self):
        st = mk_state(BASE, dead=(6,))
        st.phase = Phase.HUNTER_SHOT
        st.pending_shots = [6]
        assert legal_actions(st, 6).acts == ("shoot",)
        assert legal_actions(st, 5).acts == ()

    def test_pk_vote_restricted_to_tied_seats(self):
        st = mk_state(BASE)
        st.phase = Phase.DAY_VOTE
        st.pk_seats = (4, 5)
        la = legal_actions(st, 1)
        assert la.targets == frozenset({4, 5})

    @pytest.mark.parametrize(
        "phase_name,seat,reason",
        [("NIGHT_WOLF", 4, "not_wolf"), ("NIGHT_WITCH", 1, "not_witch"), ("NIGHT_SEER", 4, "not_seer")],
    )
    def test_a_night_phase_asked_of_the_wrong_role_names_that_reason(
        self, phase_name, seat, reason
    ):
        """能力不符不是一个空 LegalSet 了事，它得说出为什么空。

        三道守卫各守一个夜间阶段：编排器把夜里的问题发给谁，取决于这里认不认那个角色的
        能力表。若这一格退成了通用的 `no_actions_in_*`，报告里读起来像"引擎不认识这个阶段"，
        而真相是"这一座位没有这个能力"——后者才是一个能被修的名字。空集那一侧同样要钉住：
        一个说法带了非法动作，硬闸门阶段就会把一个永远给不出的选项摆上桌。
        """
        st = mk_state(BASE)
        st.phase = Phase[phase_name]
        la = legal_actions(st, seat)
        assert la.reason_if_empty == reason
        assert la.acts == () and la.targets == frozenset() and not la.allow_pass

    def test_a_live_seat_facing_last_words_may_speak_without_a_target(self):
        """遗言阶段不需要目标：把目标集造出来是编排器的事，不是合法性判据的事。

        这一支若退到通用的"这个阶段没有动作"那一行，被放逐的人就永远拿不到一次说话机会，
        而 `default_action` 会替它填一个 pass——观众看到的是一条空的遗言，不是引擎报错。
        """
        st = mk_state(BASE)
        st.phase = Phase.LAST_WORDS
        la = legal_actions(st, 4)
        assert la.acts == ("last_words",) and la.targets == frozenset()
        assert la.allow_pass and la.reason_if_empty == ""

    def test_every_act_the_assigner_can_draw_is_one_speech_may_answer_with(self):
        """闸门现在会核对 `assigned_act`，所以 persona 的词表必须是白天发言合法动作的子集。

        抽得到、闸门不发的 act 是一局里赢不了的回合：被重问一次、要求它给出一个它永远
        给不出的答案，然后落成 fallback——在报告里读起来像"这个模型不听话"，实际上是
        引擎要了个不可能的东西。词表只有一处来源（`_act_weights` 的键），所以期望值写在这
        一条里：`persona` 里曾有一行手抄的六元组，生产链没人读它，它能和键各漂各的（`#160`
        删掉了那一行，两边都钉这件事由下面的字面量期望继续钉）。
        """
        st = mk_state(BASE)
        st.phase = Phase.DAY_SPEECH
        granted = set(legal_actions(st, 4).acts)
        drawn = {k for k, v in persona._act_weights(persona.PersonaParams(), 1).items()
                 if v > 0}
        assert drawn <= granted, f"指派表里有闸门不发的 act：{sorted(drawn - granted)}"
        assert drawn == {"accuse", "defend", "align", "probe", "pivot", "listen"}

    def test_an_escalated_seat_is_never_assigned_an_act_that_cannot_name_anyone(self):
        """弃票满两轮时 phases.py 挂上 `forced_nominate` 要求点名，指派必须同向。

        两条升级机制本来各写各的：persona.py 只把 listen/align/pass 换成 accuse，而
        `defend` 同样是可以不带目标的动作。于是引擎能一边指派 `defend`、一边在提示词里
        要求点名——stand-in 桌按 §7 第 4 条把无目标的回答改成 accuse，闸门（从这次起核对
        指派）就把它拒了。100 局 mock 里有 7 局因此各出一次 fallback：一个引擎自己组装出来
        的矛盾，穿着数据质量的外衣。用例正面写"被升级的座位必须拿到一个把名字往前推的动作"
        ——升级要的是有人出局，不是有个标签。
        """
        st = mk_state(BASE)
        st.phase = Phase.DAY_SPEECH
        st.abstain_streak = {4: 2}
        legal = {s: legal_actions(st, s) for s in st.alive_seats}
        personas = {s: persona.PersonaParams() for s in st.alive_seats}
        for draw in range(200):
            act, target = persona.assign_speech_acts(
                st, st.alive_seats, personas, {}, random.Random(draw), Config(), legal)[4]
            assert act in ("accuse", "probe", "pivot") and target is not None, (
                f"第{draw}次抽样给被升级的4号指派了 {act}（target={target}）："
                f"闸门核对指派，C4 却同时要求它点名")

    @pytest.mark.parametrize("quota", [0, 1, 2])
    def test_the_listen_quota_bounds_a_round_without_guaranteeing_it(self, quota):
        """配额是上限，不是抽样偏差：用完之后 listen 必须抽不到，但也不能被保证出现。

        原先 `_act_weights` 给所有动作套 0.02 地板（防止某个 act 结构性不可达，让 M5 的
        passivity 度量成抽样表），同一个地板把配额用完后的 listen 也救回来了：实测 400 轮
        里配额 1 有 17 轮抽到两次、配额 2 有 9 轮抽到三次。而 `listen_quota_per_round`
        进 `config_hash`，两臂只差这一个数字时，McNemar 比的其实是"地板漏得多快"而不是
        配额本身。反向控制同一条断言里给：0 次的轮子必须存在（400 轮里约 64 轮），否则
        "修好了"只是把配额写成了每轮必有一个 listen。0 是第三档而不是边角料：它是"这轮不许
        有人不表态"那个处理臂，只由 `max(0, quota)` 那一行区分得出来。
        """
        st = mk_state(BASE)
        st.phase = Phase.DAY_SPEECH
        legal = {s: legal_actions(st, s) for s in st.alive_seats}
        personas = {s: persona.PersonaParams() for s in st.alive_seats}
        cfg = Config(listen_quota_per_round=quota)
        counts = [
            sum(1 for act, _ in persona.assign_speech_acts(
                st, st.alive_seats, personas, {}, random.Random(draw), cfg, legal).values()
                if act == "listen")
            for draw in range(400)
        ]
        assert max(counts) <= quota, (
            f"配额 {quota} 漏成了软目标：400 轮里 {sum(1 for c in counts if c > quota)} 轮超额")
        assert min(counts) == 0, "配额不是下限：一轮都不 listen 必须仍然可达"
        assert max(counts) == quota, f"修复过头了：配额 {quota} 一次都没用满"

    def test_the_shipped_listen_quota_is_one_pass_per_round(self):
        """出厂默认进 `config_hash`，静默改动等于换掉了整批语料的形状。

        上一用例自己传 quota，所以它测不到默认值被人挪一格；`max_days` 那条也是同样理由钉的
        （`tests/test_day_cap.py`）。
        """
        assert Config().listen_quota_per_round == 1

    def test_no_persona_on_the_sampled_range_makes_an_act_unreachable(self):
        """人格权重恒为正，所以地板是多余的——这句话得有用例守着，不能只写在注释里。

        `_act_weights` 里五个由人格决定的动作，最紧的一档是 `align` 在 conformity=0 时的
        0.15，而 `sample_persona` 抽样区间是 [0.1, 0.95]，取不到 0。地板 `max(v, 0.02)`
        因此从来没有为它们任何一档生效过，唯一被它改动过的权重就是配额用完的 listen——
        一个防不住的场景换来一个真实发生的漏。这条用例是删掉地板的凭据：将来谁把某个系数
        调到能让权重落到 0，这里会先变红，那时再决定是补地板还是改 Prior。
        """
        for agg in (0.0, 0.15, 0.5, 0.95, 1.0):
            for susp in (0.0, 0.2, 0.5, 0.9, 1.0):
                for conf in (0.0, 0.1, 0.5, 0.9, 1.0):
                    p = persona.PersonaParams(aggression=agg, suspicion_prior=susp,
                                              conformity=conf)
                    weights = persona._act_weights(p, 1)
                    bad = [k for k, v in weights.items() if k != "listen" and v <= 0]
                    assert not bad, (f"aggression={agg} suspicion={susp} conformity={conf} "
                                     f"时 {bad} 结构性抽不到")
                    assert persona._act_weights(p, 0)["listen"] == 0.0, "配额用完必须抽不到"


# -------------------------------------------------------------------- last words


@pytest.mark.parametrize(
    "death,expected",
    [
        (Death(4, day=1, night=0, cause="exiled"), True),
        (Death(4, day=1, night=1, cause="wolf_kill"), True),
        (Death(4, day=2, night=2, cause="wolf_kill"), False),
        (Death(4, day=2, night=0, cause="hunter_shot"), False),
    ],
)
def test_last_words_table(death, expected):
    assert needs_last_words(mk_state(BASE), death) == expected


# ----------------------------------------------------------------- public projection


def test_teammates_view_is_wolves_only():
    st = mk_state(BASE)
    assert st.teammates_of(1) == frozenset({7, 9})
    assert st.teammates_of(4) == frozenset()
    st.seats[7].alive = False
    assert st.teammates_of(1) == frozenset({9})
