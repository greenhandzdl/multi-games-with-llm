"""The real half of the chain, end to end, without a wire.

`HttpTransport` → `LLM` → `LlmActor` → `Agent` → `phases` → `game`, with `httpx.MockTransport`
standing in for the socket. Everything above the socket is production code here: the answers
are produced by an oracle that reads the prompt the engine actually assembled, so the parse →
gate → write path runs on bytes shaped like a model's, not like `MockActor`'s.

This is the rehearsal for the two criteria that need a live endpoint (plan §10 M2 "20 次调用
零悬挂" and M4 "真端点 10 局 0 悬挂"), plus the two failure shapes that must never reach the
corpus as behaviour: an unreachable endpoint, and a prompt that does not fit.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx

from wolfengine import cli, game, metrics
from wolfengine.config import Config
from wolfengine.state import Phase
from wolfengine.transport import HttpTransport

sys.path.insert(0, str(Path(__file__).parent))
from test_payload_shape import undeclared_keys  # noqa: E402  (形状表那把尺也量折叠标记这一格)

# The acts the gate does not expect a target for (legality.TARGETLESS_ACTS).
TARGETLESS = {"pass", "last_words", "discuss", "defend", "listen", "save"}

ACTS = re.compile(r"合法动作：([^\n（]+)")
TARGETS = re.compile(r"可选目标：(\[[^\]]*\])")
ASSIGNED = re.compile(r"act = (\w+)。")
# What a model may actually cite: the `[eNNN]` anchors the chronicle prints in front of each
# line. NOT `\be\d+\b`, which first matches the two example ids inside region A's schema
# (`如 e97、e132`) — an answer built from those gets refused as `invented_event_ids`, so an
# oracle written that way would be measuring its own carelessness.
CITE = re.compile(r"\[(e\d+)\]")
# Same trap as CITE, one sentence later: region A's schema walks the model through an example
# that starts `假设你是4号`, so `你是(\d+)号` finds that one first and every seat on the table
# answers as seat 4. This is the line `assemble.py` actually writes for the seat being asked.
SEAT = re.compile(r"你是(\d+)号。你的身份是")


def _user_text(request: httpx.Request) -> str:
    """The whole prompt, all three messages joined.

    Region C alone is not enough to answer like a model: the `[eNNN]` anchors it would cite
    live in region B, so an oracle that only read the last message could never produce a
    legal `evidence` list — and the citation gate would then be silently untested.
    """
    return "\n\n".join(m["content"] for m in json.loads(request.content)["messages"])


def oracle_answer(prompt: str) -> str:
    """Answer like a model that read its instructions: pick from what the judge offered."""
    acts = ACTS.search(prompt).group(1).split("/") if ACTS.search(prompt) else ["pass"]
    targets = ast.literal_eval(TARGETS.search(prompt).group(1)) if TARGETS.search(prompt) else []
    assigned = ASSIGNED.search(prompt)
    act = assigned.group(1) if assigned and assigned.group(1) in acts else acts[0]
    cites = CITE.findall(prompt)
    payload: dict = {"act": act, "evidence": [cites[-1]] if cites else []}
    if act not in TARGETLESS and targets:
        payload["target"] = targets[0]
    elif act not in TARGETLESS and not targets:
        # Nothing to point at: abstaining is the only answer that is legal by construction.
        payload["act"] = "pass" if "pass" in acts else acts[0]
    if "speech" in act or act in {"accuse", "probe", "pivot", "align", "vote"}:
        seat = SEAT.search(prompt)
        payload["speech"] = f"{'我是' + seat.group(1) + '号，' if seat else ''}我看{payload.get('target', 1)}号今天的话前后对不上。"
    if payload["act"] in ("accuse", "probe") and targets:
        payload["belief"] = {"suspects": [{"seat": t, "why": "票型"} for t in targets[:2]]}
    return json.dumps(payload, ensure_ascii=False)


def _chat_body(answer: str, *, cached: int | None = None) -> dict:
    usage: dict = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
            "usage": usage}


async def _play(handler, tmp_path, cfg: Config | None = None, *, seed: int = 7):
    """One game, one mock socket; the client is closed so no test leaks a connection pool."""
    cfg = cfg or Config()
    calls: list[httpx.Request] = []

    async def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    try:
        res = await game.play(cfg=cfg, deal_seed=seed, out_dir=tmp_path,
                              transport=HttpTransport(cfg, client=client))
    finally:
        await client.aclose()
    return res, calls


# ------------------------------------------------------------------ the outage must stay an outage
async def test_an_unreachable_endpoint_aborts_without_inventing_nine_seats_of_behaviour(
        key, tmp_path):
    """The most likely first live failure, and the one that used to be recorded as data.

    Before the classification fix in transport.py, `ConnectError` came back as an ordinary
    bad answer, so the gate fell through to `default_action` for every seat and the game
    finished with a real `terminal` — a transcript in which nine seats "played passively"
    while nothing was ever sent. `aborted_endpoint` is the only honest outcome, and it has
    to be visible in the *counters*, not just in the terminal string.
    """
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    started = time.monotonic()
    res, calls = await _play(boom, tmp_path)
    took = time.monotonic() - started
    assert res.terminal == "aborted_endpoint" and res.winner is None
    assert res.fallbacks == 0, "an outage must not be laundered into engine-chosen actions"
    assert res.retries == 0
    g = metrics.read_game(res.path)
    assert g.meta["actor_kinds"] == ["llm"]
    assert [e.kind for e in g.events if e.kind in ("vote", "night_action")] == []
    # 1 call + the 2 retries `LLM` is allowed, then the outage is named rather than absorbed.
    assert len(calls) == 3
    # The only test in the suite that *asserts* a real-time wait, and it says so on purpose:
    # every other retry test injects a recorder and asserts on the *values*, so nothing else
    # can tell `sleep=asyncio.sleep` from `sleep=lambda d: None` — and a zero-delay retry storm
    # is exactly what turns one flaky endpoint into ninety refused seats. 1.5 + 3.0 = the two
    # backoffs this game spends before it aborts. The 401 sibling below waits the same 4.5s
    # without claiming to: one duration assertion is enough to catch a no-op sleep, and a second
    # would only add another way for machine load to turn a green suite red.
    assert took >= 4.4, f"the game path must wait on the real backoff, not a no-op: {took:.2f}s"


# --------------------------------------------------------------------------------- the refusal
async def test_a_rejected_key_aborts_the_game_instead_of_answering_for_nine_seats(key, tmp_path):
    """The sibling of the test above, for the case where the endpoint *does* answer.

    A `ConnectError` was already classified as an outage; a 401 was not, because the predicate
    was written as "5xx or gateway HTML" while the meaning it has to carry is "this cannot be a
    fact about the model". A refused credential produces an identical refusal for every seat in
    every phase, so a game that continues past it is one transcript of `default_action` with a
    plausible-looking terminal — the exact laundering this file exists to prevent, arriving
    through a status code instead of a socket error.
    """
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    res, calls = await _play(deny, tmp_path)
    assert res.terminal == "aborted_endpoint" and res.winner is None
    assert res.fallbacks == 0, "a refused key is not nine seats that chose to pass"
    assert len(calls) == 3, "1 call + the 2 retries, then the refusal is named"
    g = metrics.read_game(res.path)
    assert [e.kind for e in g.events if e.kind in ("vote", "night_action")] == []


# -------------------------------------------------------------------------------- a whole game
async def test_a_full_game_played_through_http_reaches_a_terminal_and_parses_every_turn(
        key, tmp_path):
    res, calls = await _play(lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))),
                             tmp_path)
    assert res.terminal in ("good_win", "wolf_win", "draw_day_limit"), res.terminal
    assert res.fallbacks == 0, (
        "the oracle only ever picks from what the judge offered, so any fallback here means "
        "the prompt the model is shown and the set the gate checks have drifted apart")
    assert len(calls) > 20, "a game that made 20 calls never left the first night"

    g = metrics.read_game(res.path)
    decisions = [e for e in g.events if e.actor is not None and e.kind in
                 ("vote", "night_action", "speech", "last_words", "wolf_chat")]
    assert decisions, "no seat ever got asked"
    assert all(e.response.get("text") for e in decisions)
    # rung >= 0 is what separates this from a mock table: it proves the text went through the
    # bracket scanner and the repair ladder. `fallback == 0` says the *model's* answer is what
    # the game recorded, not an engine default that happened to be legal. Provenance lives in
    # `payload.meta` (a derived view), while `response` holds the transport's own accounting.
    assert all(e.payload["meta"]["rung"] >= 0 for e in decisions)
    assert all(not e.payload["meta"]["fallback"] for e in decisions)
    assert all(e.result["ok"] and not e.result["fallback"] for e in decisions)
    assert all("latency_s" in e.response and "attempts" in e.response for e in decisions)


# The same ruler `tests/test_cli.py` spells by hand. Two copies, deliberately: each file's guard
# then goes red on its own, so a phase rename cannot be "fixed" by editing one copy to match
# whatever production now says.
SPEECH_BY_HAND = {"day_speech", "day_pk_speech", "last_words"}


async def test_every_turn_sends_the_max_tokens_its_phase_is_priced_at(key, tmp_path):
    """`token_budget_for` 必须同时是普查印出来的数和真正发出去的数，两侧各钉一次。

    普查那侧在 `tests/test_cli.py::test_the_census_prices_a_game_in_calls_tokens_and_completion_budget`；
    这一条钉请求字节那侧，读数取自落盘日志的 `Event.request`，不是从函数返回值上自证——演员要是
    退回内联三元组、或把某个阶段算错档，发出去的和印出来的就不是一个数，而每局墙钟正是后者的和。
    """
    res, _ = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path)
    g = metrics.read_game(res.path)
    priced = [(e.phase, e.request.get("max_tokens")) for e in g.events if e.request]
    assert len(priced) >= 20, f"带 `request` 的事件太少（{len(priced)}），断言会空转"
    cfg = Config()
    for phase, sent in priced:
        assert phase in {p.value for p in Phase}, f"日志里有枚举不认识的阶段 {phase!r}"
        want = cfg.max_tokens_speech if phase in SPEECH_BY_HAND else cfg.max_tokens_action
        assert sent == want, f"{phase} 发出了 {sent}，普查会按 {want} 计价"
    seen = {ph for ph, _ in priced}
    assert seen & SPEECH_BY_HAND, "整局没走到发言阶段，上面的循环是自证"
    assert seen - SPEECH_BY_HAND, "整局没走到动作阶段，上面的循环是自证"


async def test_a_full_ladder_is_the_temperature_that_ships_seat_by_seat(key, tmp_path):
    """梯子非空时它就是每座的那一格：短梯子夹紧在最后一档，全局默认一个字节都不许漏上去。

    plan §7 的反塌缩设计把"逐座升温"写成了规则，可这格温度此前只活在 `LlmActor` 的一行下标里，
    从没有一条用例读过线上那个字段。两臂温度相同、报告却写"处理轴：temperature_ladder"的那种批，
    就是这一格要拦的。座位号取自落盘日志的 `Event.actor` 而不是提示词：区域 A 的说明书里有一句
    固定的"你是4号"，`re.search` 先撞上它，按文本归座会把五十六次调用全记到 4 号头上。
    """
    cfg = Config(temperature=0.2, temperature_ladder=(0.7, 1.1))
    res, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path, cfg)
    g = metrics.read_game(res.path)
    decided = [(e.actor, e.request["temperature"]) for e in g.events if e.request]
    assert {a for a, _ in decided} >= {1, 2, 9}, "整局没走满九座，上面的循环是自证"
    for seat, sent in decided:
        assert sent == (0.7 if seat == 1 else 1.1), f"{seat} 号发出了 {sent}，梯子是 (0.7, 1.1)"
    assert sorted(json.loads(r.content)["temperature"] for r in calls) \
        == sorted(t for _, t in decided), "记下来的温度和发出去的温度不是一回事"


async def test_the_stub_oracle_speaks_as_the_seat_it_is_asked_to_be(key, tmp_path):
    """桩判官的自我归属要跟着**被问的那一座**走。

    区域 A 的说明书里钉着一句"假设你是4号"，而 `oracle_answer` 取的是第一个命中——于是九座全自报
    4 号。这一格不只是难看：`template_top1` 量的就是"本轮发言里最共享的那段模板占多少"，九份共享
    同一个 `我是4号，` 前缀会被它当成行为。座位号取自落盘日志的 `Event.actor`，不从提示词里再推
    一遍——拿被检的那条判据当参照，这个测试就永远绿。
    """
    seen: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        text = oracle_answer(_user_text(r))
        seen.append(text)
        return httpx.Response(200, json=_chat_body(text))

    cfg = Config()
    res, _ = await _play(handler, tmp_path, cfg)
    g = metrics.read_game(res.path)
    pairs = [(e.actor, json.loads(t).get("speech", ""))
             for e, t in zip([e for e in g.events if e.request], seen)]
    spoken = [(a, s) for a, s in pairs if "我是" in s]
    assert len(spoken) >= 20, f"只有 {len(spoken)} 份自报座位的发言，下面的判断是自证"
    # 上一行是聚合地板：某一整座掉出断言（归属句式对某一座不成立）时它照样绿。
    silent = set(range(1, cfg.seat_count + 1)) - {a for a, _ in spoken}
    assert not silent, f"这些座位一份自我归属都没进断言，那一整座等于没测：{sorted(silent)}"
    wrong = [(a, s.split("，")[0]) for a, s in spoken if not s.startswith(f"我是{a}号")]
    assert not wrong, f"{len(wrong)}/{len(spoken)} 份发言自报的不是被问的那一座：{wrong[:3]}"


async def test_an_empty_ladder_ships_the_global_temperature(key, tmp_path):
    """`--set B.temperature=0.6` 是 `docs/comparison.md` 的招牌例子，前提是那个数真的上线。

    空梯子的语义是"没有逐座覆盖这回事"，于是每一次调用都该发同一个全局值。这一格以前不是语义
    问题而是崩溃问题：`temperature_ladder=()` 时下标 `-1` 当场 `IndexError`，一局都开不了。而
    `Config.temperature` 这个名字在 `src/` 里被提起的每一处都在字里行间——`--set` 的例子、
    `--axis` 的例子、帮助文本——散文不是读取点，于是两臂字节相同也过得去轴守卫（`#76`）。
    """
    cfg = Config(temperature=0.35, temperature_ladder=())
    _, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path, cfg)
    assert calls, "一局没发出任何请求"
    assert {json.loads(r.content)["temperature"] for r in calls} == {0.35}


async def test_the_log_alone_yields_the_budget_to_used_ratio_the_census_is_waiting_on(key, tmp_path):
    """普查印的是上限；一局真跑完之后，兑现率必须只从落盘日志里算得出来。

    `--dry-run` 的"完成预算"和每局墙钟都是 `max_tokens` 之和，是个上限；上限要变成期望，需要
    "问了它多少"和"它实际答了多少"在**同一份产物**里对账。这里两头都从磁盘读：`m7_cost_profile`
    的读数，加一条手写算术（同一批事件、同样的求和，但不共用那个函数）——两者不一致，就是 m7
    在看日志之外的东西，或者某个阶段压根没把预算记进 `request`。
    """
    res, _ = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path)
    g = metrics.read_game(res.path)
    m7 = metrics.m7_cost_profile(g.events)
    billed = [e for e in metrics.decisions(g.events) if e.response.get("latency_s") is not None]
    assert m7["n_calls"] == len(billed) >= 20, "整局没走到有记账的调用，下面的比值是空的"
    assert m7["asked_calls"] == len(billed), (
        f"{len(billed)} 次调用里只有 {m7['asked_calls']} 次记下 `max_tokens`：分母缺角")
    asked = sum(e.request["max_tokens"] for e in billed)
    used = sum(int(e.response.get("completion_tokens") or 0) for e in billed)
    assert (m7["asked_total"], m7["completion_total"]) == (asked, used)
    assert m7["fill_rate"] == round(used / asked, 4), m7
    assert 0 < m7["fill_rate"] < 1, (
        f"兑现率 {m7['fill_rate']}：Oracle 只说法官给过的短答案，落到 1.0 就是预算被顶满，"
        "那要当上限选错来处理，不是当简洁来处理")


# ------------------------------------------------- 端点报了 cached_tokens，然后到得了谁
def _audit(path) -> dict:
    """跑一次 `wolf audit`，把那一行 JSON 拿回来。走命令行而不是内部函数：这一格要钉的是
    "读日志的人看得见"，而 audit 的记录里少一个键、或者那个键被写死成 `None`，只有从这里才看得出来。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["audit", str(path)]) == 0
    return json.loads(buf.getvalue())


async def test_a_cached_prefix_reported_on_the_wire_reaches_the_audit_cell(key, tmp_path):
    """桩回 `prompt_tokens_details.cached_tokens` 时，那一格必须有数——中间四只手没有一条链同时穿过。

    `transport.usage_from()` → `CallResult.as_response_dict()` → `Event.response` 落盘读回 →
    `metrics.prefix_cache_reuse()` → `cli` 的 audit 记录。每一只手各自有用例，可它们首尾不相接：
    度量那一页全部是手写 `Event`（`tests/test_prefix_cache.py` 从不过网络），网络那一页最远只走到
    `TransportResult.raw_usage`（`tests/test_transport.py`）。也就是说"一局真跑完之后 §5 那笔折扣
    读不读得出来"这个问题，今天只有真端点上的 M0 复采能回答——而它恰好是唯一一个此刻回答不了的。
    数字选 700/900：桩每次都回同样的 usage，所以比值必须是 `700/900`，既不是逐相平均，也不是只算
    第一波。
    """
    res, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)), cached=700)),
        tmp_path)
    assert calls, "一次都没发出去，下面的读数就是空的"
    g = metrics.read_game(res.path)
    timed = metrics.timed_decisions(g.events)
    assert len(timed) > 20, f"只量到 {len(timed)} 次调用，第一夜都没走完"
    assert all(e.response["usage"]["cached_tokens"] == 700 for e in timed), \
        "线上报的那个数没落到盘上"

    out = metrics.prefix_cache_reuse(g.events)
    assert out["reported"] == out["calls"] == len(timed) and out["silent"] == 0, out
    assert out["unpairable"] == 0, "分母缺角：报了 cached 却没报 prompt 的那些次被蒸发了"
    assert out["cached_tokens"] == 700 * out["calls"], out
    assert out["reuse_ratio"] == round(700 / 900, 4), out

    printed = _audit(res.path)["prefix_cache"]
    assert printed["reuse_ratio"] == out["reuse_ratio"], printed
    assert printed["cached_tokens"] == out["cached_tokens"], printed


async def test_a_wire_that_never_mentions_cached_tokens_leaves_that_cell_null_not_zero(
        key, tmp_path):
    """同一局、同一个 oracle，只把 `prompt_tokens_details` 那一支收掉：读数必须从 0.7778 变成 `null`。

    这一条是上一条的反面，缺了它上一条就不是"从线上来的一次读数"：如果 `cached_tokens` 其实是
    从别处（写死的 0、或 `prompt_tokens` 自己）来的，上一条照样绿。收掉线上那一支之后，三个平铺
    计数仍然要在（它们是有主的），只有 `cached_tokens` 整块缺席——"这台端点一个字都没说"和"说了，
    一次都没复用"是两句不同的话，后者才是 §5 的经济性要用的证据。
    """
    res, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path)
    assert calls
    g = metrics.read_game(res.path)
    timed = metrics.timed_decisions(g.events)
    assert len(timed) > 20
    assert all("cached_tokens" not in e.response["usage"] for e in timed)
    assert all(e.response["usage"]["prompt_tokens"] == 900 for e in timed), \
        "把缺席写成整块丢掉：平铺计数也是白名单里的"

    out = metrics.prefix_cache_reuse(g.events)
    assert out["reported"] == 0 and out["silent"] == out["calls"] == len(timed), out
    assert out["reuse_ratio"] is None and out["cached_tokens"] is None, out
    printed = _audit(res.path)["prefix_cache"]
    assert printed["reuse_ratio"] is None, printed


async def test_a_citation_of_an_event_the_seat_was_shown_is_accepted(key, tmp_path):
    """The plan's highest-value field (§4 `evidence`), tested where it is enforced.

    The oracle cites ids it read out of its own prompt, which is what a model that behaves
    does; what this pins is that the engine agrees — the gate's own accounting says "valid",
    not "invented". A drift between the prompt and `Percept.id_set` would show up here as
    every real citation being refused.
    """
    res, _ = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))), tmp_path)
    g = metrics.read_game(res.path)
    stats = [e.payload["meta"]["citation_stats"] for e in g.events
             if e.actor is not None and "meta" in e.payload]
    cited = [s for s in stats if s["cited"]]
    assert cited, "the oracle cited nothing; the path under test never ran"
    assert all(not s["invented"] for s in cited)
    assert all(s["valid"] for s in cited), "a citation from the seat's own prompt was refused"


async def test_an_invented_citation_is_refused_and_the_refusal_is_kept_verbatim(key, tmp_path):
    """`attempts[]` is the dataset, and this is the only way to see it fill up.

    A model that numbers events that never happened is refused even during speech
    (legality.py:96): the citation is addressed to the engine, not to the other players. The
    refused text must survive next to the reason, because rejected-plus-accepted is the
    preference pair the corpus is worth having, and this endpoint cannot be replayed.
    """
    res, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body('{"act":"pass","evidence":["e424242"]}')),
        tmp_path)
    g = metrics.read_game(res.path)
    refused = [e for e in g.events if e.attempts]
    assert refused, "an invented event id was accepted"
    assert res.retries > 0 and res.fallbacks > 0
    first = refused[0].attempts[0]
    assert "e424242" in first["raw"]
    assert any("invented_event_ids" in x for x in first["violations"]), first["violations"]
    # The accepted record must not be the invented one, and the fallback has to say so.
    assert refused[0].result["fallback"] == 1


# --------------------------------------------------------------- the two things never sent
async def test_a_game_that_retries_past_its_budget_stops_instead_of_finishing(
        key, tmp_path):
    """`max_retries_total` is the plan's cost guard (§6 全局预算), and a guard nothing reads is
    not a guard. Refusing every seat is how a run spends 30+ re-asks on one day.
    """
    cfg = Config()
    cfg.max_retries_total = 3

    def never_legal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_body('{"act":"check","target":2}'))

    res, calls = await _play(never_legal, tmp_path, cfg)
    assert res.terminal == "aborted_budget", (
        f"a game that spent {res.retries} re-asks against a budget of 3 ran to {res.terminal}")
    assert res.retries > cfg.max_retries_total
    # Stopping early is not the same as a healthy finish: the game must not look like a win.
    assert res.winner is None


async def test_a_prompt_over_the_ceiling_is_never_sent_and_the_lever_is_pulled_first(
        key, tmp_path):
    """The first test in the repo that drives `shrink > 0`.

    Every other file asserts `shrinks == 0` — which is the right thing for a healthy budget
    table and no coverage at all of the lever itself. Here the ceiling is absurdly small, so
    the plan's sacrifice order has to run: halve B2's verbatim window twice, and only then
    give up on the turn. Zero bytes may reach the socket, because a 400 on a prompt we
    already know is too long is a wasted round trip and a retry loop.
    """
    cfg = Config()
    cfg.tokens = dataclasses.replace(cfg.tokens, absolute_ceiling=60)
    res, calls = await _play(lambda r: httpx.Response(200, json=_chat_body('{"act":"pass"}')),
                             tmp_path, cfg=cfg)
    assert calls == [], "an over-ceiling prompt must be caught before the call, not by the endpoint"
    assert res.shrinks > 0 and res.context_overflows > 0
    assert res.terminal != "aborted_endpoint"
    # Skipping the turn is the engine's decision, so it is a fallback and it is marked.
    assert res.fallbacks > 0


async def test_the_chronicle_actually_folds_inside_a_played_game(key, tmp_path):
    """计划 §5 的主杠杆第一次在一整局里被驱动。

    `shrinks > 0` 有用例，`compactions > 0` 没有：100 局 mock、以及把 `cfg.tokens` 四档全
    调小，落盘的都是 `request.compactions == 0`——真正决定 B 区预算的是 `regions.b2`，不是
    全局天花板。把 B 自己的预算压小，几何折叠就必须中途接管，而这一局仍然要打完。
    """
    cfg = Config()
    cfg.regions = dataclasses.replace(cfg.regions, b2=200, b1=200)
    res, calls = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))),
        tmp_path, cfg=cfg)
    assert calls, "折叠是为了把 prompt 发出去，不是为了不发"
    assert res.terminal in ("good_win", "wolf_win", "draw_day_limit"), res.terminal
    g = metrics.read_game(res.path)
    rounds = [int(e.request.get("compactions") or 0) for e in g.events if e.request]
    assert max(rounds) >= 1, f"region B 从未折叠（最多 {max(rounds)} 轮）"
    assert max(rounds) <= 5, "plan §11 的预算是 ≤5 次/局，超过就是在逐条重写缓存前缀"
    deep = max((e for e in g.events if e.request),
               key=lambda e: int(e.request.get("compactions") or 0))
    b = deep.request["messages"][1]["content"]
    assert "：发言" in b, "折叠轮数涨了，但 B 里没有出现被折叠的那一天"
    assert len(CITE.findall(b)) >= 4, \
        f"折到只剩 {len(CITE.findall(b))} 条逐条：座位看不见刚才发生了什么，folding 的底线没了"


async def test_a_length_400_never_resends_the_same_bytes(key, tmp_path):
    """§6: "400 绝不无脑重发（死循环）" — the remedy is *fewer* bytes, so an identical resend
    is the loop the plan names, just with extra latency.

    The endpoint here answers 400 to everything, which is the worst case for this rule: the
    assembler pulls its lever, and if the lever had nothing left to cut the next prompt is
    byte-for-byte the one that was just refused. That second send must not happen.
    """
    sent: list[str] = []

    def too_long(request: httpx.Request) -> httpx.Response:
        sent.append(_user_text(request))
        return httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}})

    res, _ = await _play(too_long, tmp_path)
    adjacent = sum(1 for a, b in zip(sent, sent[1:]) if a == b)
    assert adjacent == 0, f"{adjacent} sends were byte-identical to the one just refused"
    assert res.context_overflows > 0 and res.terminal != "aborted_endpoint"


async def test_the_overflow_counter_reports_turns_not_attempts(key, tmp_path):
    """`overflow=` in the summary line is a *turn* count, and metrics reads it per event.

    The M3 criterion is literally "0 次 context 400"; a number that counts each retry of one
    lost turn makes it three times worse than reality, and stops agreeing with the log the
    same run wrote. Both sides have to be the same quantity.
    """
    def too_long(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}})

    res, calls = await _play(too_long, tmp_path)
    g = metrics.read_game(res.path)
    turns = [e for e in g.events if e.actor is not None
             and e.payload.get("meta", {}).get("context_overflow")]
    assert turns, "no turn was recorded as lost, yet the endpoint refused every call"
    assert res.context_overflows == len(turns), (
        f"summary says {res.context_overflows}, the log says {len(turns)}")
    assert res.shrinks > 0, "the lever moved up to twice per lost turn but recorded nothing"


# ---------------------------------------------------------------- the degraded verdict is an artifact
def _game_over(g: metrics.Game) -> dict:
    return [e for e in g.events if e.kind == metrics.Kind.GAME_OVER][-1].payload


async def test_a_degraded_game_records_the_verdict_in_the_log_not_only_in_ram(key, tmp_path):
    """plan §141-143：`fallback` 超过阈值的局要**当场**记 `degraded_game=1`，供报告点名。

    这个判定原本只活在 `GameResult.degraded` 上——一次函数返回值的寿命。日志、batch 的行、
    run_manifest、compare 报告四处都看不见它，于是"这局有一半的动作是引擎替座位做的主"在
    产物链上不可问：拿着单个 jsonl 复盘的人（`wolf audit`、直播渲染、三个月后的自己）读不到
    任何痕迹，只能重新数一遍 fallback 并**重新决定**阈值算不算超——那正是"事后悄悄剔除"。
    阈值压到 0，用最省事的退化来源（每个座位都被拒到 fallback）驱动它。
    """
    cfg = Config()
    cfg.degraded_game_fallbacks = 0

    def never_legal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_body('{"act":"check","target":2}'))

    res, _ = await _play(never_legal, tmp_path, cfg)
    assert res.fallbacks > 0 and res.degraded
    g = metrics.read_game(res.path)
    over = _game_over(g)
    assert over["degraded_game"] is True, over
    assert over["degraded_threshold"] == 0, \
        "阈值要跟着判定一起落盘：单个日志文件里没有 config_hash 之外的配置，不写就没人能复核"
    # One predicate, two views. The terminal verdict must be recomputable from the per-turn marks
    # the same run wrote, not from a counter that only ever existed in RAM.
    marked = sum(1 for e in g.events if e.payload.get("meta", {}).get("fallback"))
    assert marked == res.fallbacks > over["degraded_threshold"]
    assert g.degraded_game is True, "reader and writer must agree on the same bytes"


async def test_a_clean_game_records_the_verdict_as_false_rather_than_omitting_it(key, tmp_path):
    """一局没退化，也要在日志里说"没退化"，而不是什么都不写。

    缺字段和有值在读取端永远要分开（下面那条用例就是为这个），所以写的时候不能省：一份
    `degraded_game` 缺失的**新**日志意味着某个终局路径漏写了这一格，而它读起来会和"这是
    字段落地之前跑的旧批次"一模一样——两种完全不同的解释，同一个症状。

    阈值给 0 而不是出厂的 12：一局干净的 mock 桌 `fallbacks == 0`，于是"0 > 0 为假"正好把 `>`
    和 `>=` 分开。写成 12 的话这一局离边界有 12 次 fallback 那么远，判据写成 `>=` 也照样
    绿——`>=` 与 `>` 的 off-by-one 在日子上限上已经漏过一次（见 `tests/test_day_cap.py`）。
    """
    cfg = Config()
    cfg.degraded_game_fallbacks = 0
    res, _ = await _play(
        lambda r: httpx.Response(200, json=_chat_body(oracle_answer(_user_text(r)))),
        tmp_path, cfg=cfg)
    assert res.fallbacks == 0 and not res.degraded, "fixture 失效：这一局不干净"
    over = _game_over(metrics.read_game(res.path))
    assert over["degraded_game"] is False, "the verdict is missing, not negative"
    assert over["degraded_threshold"] == 0
    assert metrics.read_game(res.path).degraded_game is False


def test_the_shipped_degraded_threshold_is_the_pre_registered_twelve():
    """plan §143 预注册的是 12，而现在这个数字会出现在每份日志、每份对比报告里。

    钉出厂值的理由和 `listen_quota_per_round`、`max_days` 一样（见 `tests/test_rules.py`）：
    默认进 `config_hash`，挪一格就等于换掉整批语料的判定规则。多一层的是——上面两条用例都把
    阈值当**参数**传（0 和出厂值），所以它们全都测不到有人把 12 改成 13，而对外声明过的剔除
    规则改一次就要重新跑一次批次。
    """
    assert Config().degraded_game_fallbacks == 12


# ------------------------------------------------------------------ the fold is an artifact
FOLDED_DAYS = re.compile(r"第(\d+)天：发言")


def _squeezed(ceiling: int | None = None) -> Config:
    """A chronicle that must fold, and optionally a ceiling nothing fits under.

    The two knobs are separate on purpose: `regions.b2` decides whether the engine folds the
    chronicle, `tokens.absolute_ceiling` decides whether the result was ever sent. A test that
    only had the first cannot tell "the model saw a folded chronicle" apart from "the turn was
    skipped".
    """
    cfg = Config()
    cfg.regions = dataclasses.replace(cfg.regions, b2=200, b1=200)
    if ceiling is not None:
        cfg.tokens = dataclasses.replace(cfg.tokens, absolute_ceiling=ceiling)
    return cfg


def _oracle(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_chat_body(oracle_answer(_user_text(request))))


def _markers(g) -> list:
    return [e for e in g.events if e.kind == metrics.Kind.COMPACTION]


def _region_b_of(e) -> str:
    msgs = (e.request or {}).get("messages") or []
    return msgs[1]["content"] if len(msgs) > 1 else ""


async def test_a_fold_the_model_was_shown_leaves_a_marker_in_the_log(key, tmp_path):
    """plan §83 要 compress.py 写 compaction 事件，为的是复盘的人能看出模型看的是折过的编年史。

    今天这件事只在 `request.messages` 里以整段 prompt 的形式存着：要回答"这局折叠了几次"就得
    把 49 段 prompt 各扫一遍。标记把它变成一格可读、可数、可渲染的事件。
    """
    res, _ = await _play(_oracle, tmp_path, cfg=_squeezed())
    g = metrics.read_game(res.path)
    markers = _markers(g)
    assert markers, "编年史折了，日志里没有一格说模型看到的是折过的版本"
    assert not undeclared_keys(markers), "折叠标记的键没写进 events.Kind 的形状表"
    for m in markers:
        p = m.payload
        assert p["window"] >= 4 and p["folded_days"], p
        assert FOLDED_DAYS.findall(p["summary"]) == [str(d) for d in p["folded_days"]], \
            "summary 与 folded_days 是同一件事的两份说法，对不上就是自相矛盾"
        assert m.visibility == "all", "哪一局看到过折叠是全局事实，不是某个座位的事"
        assert m.actor is None, "折叠是引擎的决定，不是哪个座位的发言"


async def test_one_fold_state_leaves_one_marker_however_many_seats_reach_it(key, tmp_path):
    """标记的粒度是"B1 摘要被改写过没有"，不是"谁被展示了"，也不是"B 区长了几条"。

    一份 B1 摘要 = 一次前缀冲洗（实测 3.44× 的 prefill 重付），所以它必须被记一格；9 个座位共用
    同一份 B 区字节，所以它只记一格。而 B2 只往后 append 的那些回合**不算冲洗**：按天锚定之后
    逐条留下的条数每回合都在涨（那正是缓存还在），把条数并进幂等键就会把一次改写记成 15 次
    —— 改之前实测 15 条标记 / 2 个折叠状态，账面上凭空多出 13 次根本不存在的冲洗。
    """
    res, _ = await _play(_oracle, tmp_path, cfg=_squeezed())
    g = metrics.read_game(res.path)
    shown: set[str] = set()
    folded_prompts = 0
    for e in g.events:
        b = _region_b_of(e)
        if "== 已折叠 ==" not in b:
            continue
        folded_prompts += 1
        shown.add(b.split("== 已折叠 ==\n", 1)[1].split("\n== 最近发言 ==", 1)[0])
    markers = _markers(g)
    assert folded_prompts > len(shown), "这一局的折叠前缀只有一份，去重那条断言是空的"
    assert {m.payload["summary"] for m in markers} == shown, \
        "标记点名的折叠摘要和 prompt 里真出现过的不是同一批"
    assert len(markers) == len(shown), "同一份 B1 摘要被 9 个座位各写了一条"
    # 逐条留下的条数仍然要落在 payload 里：它是"这次改写之后座位还能看住多少条"的唯一记录。
    assert all(isinstance(m.payload["window"], int) for m in markers)


async def test_a_marker_is_public_but_never_becomes_chronicle(key, tmp_path):
    """B 区摘要编年史，标记摘要 B 区——让它进编年史就是让一段摘要去摘要自己。

    更贵的是缓存：一格标记占一个 seq，插在编年史中间就把前缀改了，而 plan §5 的整笔收益
    都押在 B 的字节逐轮只往后长。
    """
    res, _ = await _play(_oracle, tmp_path, cfg=_squeezed())
    g = metrics.read_game(res.path)
    markers = _markers(g)
    assert markers, "fixture 失效：这一局没折叠"
    from wolfengine.info import eid

    ids = {eid(m.seq) for m in markers}
    for e in g.events:
        b = _region_b_of(e)
        if not b:
            continue
        assert "法官：[折叠]" not in b, "标记被渲染进了编年史"
        assert not (ids & set(CITE.findall(b))), \
            f"折叠标记的 id 进了 B 区：{sorted(ids & set(CITE.findall(b)))}"


async def test_a_prompt_that_was_never_sent_leaves_no_marker(key, tmp_path):
    """没过天花板的那一版 prompt 从没进过网络，所以它没"给谁看过"任何东西。

    对照着看才有意义：放开天花板就必须有标记（下面第二段），否则第一段只是"这一局没折叠"。
    """
    res, calls = await _play(_oracle, tmp_path, cfg=_squeezed(ceiling=50))
    assert not calls and res.context_overflows > 0, "fixture 失效：这一局真的发出去了"
    assert _markers(metrics.read_game(res.path)) == [], \
        "标记的主语是「模型看到了折叠后的编年史」，一次都没发的局里不该有这句话"

    res2, _ = await _play(_oracle, tmp_path / "open", cfg=_squeezed())
    assert _markers(metrics.read_game(res2.path)), "对照组没有标记：上面那条断言是空的"


# --------------------------------------------- the counter the shipped wiring never reached
async def _play_on_actors(handler, tmp_path, cfg: Config, *, seed: int = 7):
    """CLI 走的这一条：外部把 `actors` 交进来，`transport` 留空。

    `play()` 只在 `actors is None` 时自己造 `LLM`，所以挂在它本地变量上的计数在这条路上是零——
    这正是要拿这个函数照出来的那条分岔。
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        actors = cli._llm_actors(cfg, HttpTransport(cfg, client=client))
        return await game.play(cfg=cfg, deal_seed=seed, out_dir=tmp_path, actors=actors)
    finally:
        await client.aclose()


async def test_the_token_total_the_cli_prints_is_the_one_the_log_records(key, tmp_path):
    """汇总行的 `completion=` 与文件里 Σ `response.completion_tokens` 必须是同一个数。"""
    res = await _play_on_actors(_oracle, tmp_path, Config())
    recorded = sum(int((e.response or {}).get("completion_tokens") or 0)
                   for e in metrics.read_game(res.path).events)
    assert recorded > 0, "夹具没往 usage 里写数：这一条就没在钉任何东西"
    assert res.completion_tokens == recorded, \
        f"engine says {res.completion_tokens}, the file says {recorded}"
    assert f"completion={recorded}" in cli._summary_line(res), cli._summary_line(res)


async def test_the_completion_ceiling_stops_the_table_the_cli_sits_at(key, tmp_path):
    """`max_game_completion_tokens`（plan §6 全局预算）在出货路径上必须真的能停下一局。"""
    cfg = Config()
    cfg.max_game_completion_tokens = 100
    res = await _play_on_actors(_oracle, tmp_path, cfg)
    assert res.terminal == "aborted_budget", (
        f"花了 {res.completion_tokens} token 对上上限 100，这局却收在 {res.terminal}")
    assert res.winner is None, "提前停下不等于打赢了"
    g = metrics.read_game(res.path)
    note = [str((e.payload or {}).get("text", "")) for e in g.events
            if e.kind == metrics.Kind.PHASE and "预算耗尽" in str((e.payload or {}).get("text", ""))]
    assert len(note) == 1, note
    assert f"completion_tokens={res.completion_tokens}" in note[0], note[0]
