"""`wolf` — the whole public surface.

Only `run` and `batch` need an endpoint; the rest read a log that already exists:

* `run` plays a game and writes the JSONL. `--mock` plays it without an endpoint, which is
  the configuration CI runs on; `--dry-run` is the budget tool (plan §11: assemble every
  prompt, land it on disk, make zero API calls).
* `replay` prints a chronicle from a JSONL that already exists, and `--seat`/`--god` choose
  whose eyes the printout has.
* `audit` answers "what did this game cost, and how often did the engine paper over the
  model" for one file; `gate` says what `batch`/`compare` say over many, about existing logs.
* `export` writes the single shareable HTML file (plan §9), and `watch` tails a game that is
  still being played. Both read the log and nothing else: if the endpoint is offline tomorrow
  the demo still works, because the log — not a re-run — is the artifact.
* `batch` plays N games × K config arms on paired deal seeds, and `compare` either refuses
  that batch or reports it. The pair is the whole of plan §8's 两配置对比 protocol: the exit
  code says which of the two happened (0 = a verdict, 1 = a refusal, 2 = the command itself
  was wrong), so a script can branch without parsing prose.

There is deliberately one renderer per concern, shared by them all: `render_chronicle` for the
text timeline, `render_html`/`render_live` for the two views. A live view and a post-mortem that
format events differently are two truths about the same file, and the whole point of the log is
that there is only one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from . import batch, metrics, render_html, render_live
from .compress import render_line
from .config import Config, ConfigError
from .events import (Event, EventLog, Kind, LogDamage, empty_notice, meta_notice, seq_damage,
                     seq_notice, torn_notice, roster_notice)
from .game import DRAW_DAY_LIMIT, GameResult, play
from .info import percept_for

PRIVATE_HINT = "〔私有〕"
FALLBACK_HINT = "〔引擎代打〕"


def build_config(name: str = "default") -> Config:
    """One place where a Config is made, so a batch axis is a diff of two of these."""
    cfg = Config()
    if name == "test":
        cfg.temperature_ladder = (0.9,)
    return cfg


def make_actors(cfg: Config, seed: int, *, mock: bool, human: int | None = None):
    """Seat -> actor. `mock` synthesises instead of replaying a script: an empty script makes
    every seat fall back to the engine, and the timeline then proves only that the state
    machine does not crash. See MockActor's two-mode docstring.

    `human` hands one chair to a person (`#123`). It is a *parameter of the roster*, not a line
    in `cmd_run` that overwrites a dict entry: the mock table and the real table both come
    through `_roster`, so there is exactly one place where "this seat is typed into" is decided.
    """
    from .actors import MockActor

    if mock:
        return _roster(cfg,
                       lambda s: MockActor(s, synthesize=True,
                                           rng=random.Random(seed * 100 + s)),
                       human=human)
    raise ValueError("make_actors(mock=False) 需要 transport；由 cmd_run 构造")


# ------------------------------------------------------------------------------- run
def _run_and_close(coro_factory, transport):
    """One event loop for the requests *and* for `aclose()`, because that is the only pairing
    that works: `httpx.AsyncClient` binds its sockets to the loop that opened them, so closing
    from a second `asyncio.run` reaches into a closed loop and dies with `RuntimeError: Event
    loop is closed`. Dying inside a `finally` is the expensive part — the traceback replaces
    the exit code a wrapper script branches on. Measured 2026-09-22T05:23:33Z: a game that
    finished (`draw_day_limit`, 60 events) returned 1.
    """
    async def body():
        try:
            return await coro_factory()
        finally:
            if transport is not None:
                await transport.aclose()
    return asyncio.run(body())


def _games_error(games: int) -> str | None:
    """One floor for both verbs that take `--games`. Zero games produce nothing, and `batch`'s
    summary line then reads `pair_keys[0]` on an empty list — the crash, not the verdict, would
    hand back the exit code. Checked before a directory is made: an empty batch folder is still a
    folder `compare` will open.
    """
    return None if games >= 1 else (f"--games 要至少 1 局（收到 {games}）："
                                    "不足 1 局什么都不产，比较也没有分母")


def _seat_error(seat: int | None, path: Path) -> str | None:
    """`--seat` names a chair, and the roster it has to be in comes from the file — not from a
    second copy of the 9. `render_html.seats_of` is the same reader the vote matrix draws its
    rows from.

    Before this, `--seat 10` printed the public timeline with exit code 0, byte-identical to
    leaving `--seat` out: a seat number past the table is the command being wrong, and rc 0 is a
    verdict. Checked before a line is printed for the same reason #58 checks `--games` before a
    directory is made.

    Quiet when there is no roster to check against — a file with no 开局记录 gets `empty_notice`'s
    sentence, and "this file has no seat 42" would be a claim about a table nobody has been shown.
    """
    if seat is None:
        return None
    seats = render_html.seats_of(EventLog.read_records(path)[0])
    if not seats or seat in seats:
        return None
    return (f"--seat 要在这局的名册里（{seats[0]}-{seats[-1]}，共 {len(seats)} 席），收到 {seat}："
            "越界的座位号是命令写错了，不是引擎拒绝出结论")


def _human_error(cfg: Config, seats: list[int], *, dry_run: bool) -> str | None:
    """`--human` 的三句拒绝，全部说完在磁盘被碰之前（次序是 `#58`/`#59` 立的那条：命令本身不对
    就 rc 2，不许留下空目录、也不许留下一个"跑了一半"的转储）。

    三条各自拦的是不同的错法，其中两条拦的是**引擎无法察觉**的那种：

    * 两个 `--human`：一桌只有一个键盘。`asyncio.to_thread(input)` 从两个座位同时读同一个 stdin，
      读回来的是两个人半句话拼成的一行——日志里会显示两席都"答了"，答的却是谁都没说过的那句。
      取最后一个（argparse 的默认行为）等于让命令行决定"哪一席坐着人"。
    * `--dry-run` 加真人：转储的每一行都是"模型这一席会读到什么"，而这一席不由模型读。让它进去，
      `--dry-run` 的全部产出就掺了一席假账。
    * 越界的座位号：名册是 `_roster` 按 `cfg.seat_count` 摆的，多出来的那一席不会有人坐，
      而 `--human 10` 会被静默忽略成"这局没有真人"——演示现场最贵的一种假绿。
    """
    if not seats:
        return None
    if len(seats) > 1:
        return (f"--human 只给一个座位（收到 {seats}）：一桌只有一个键盘，"
                "两席同时读 stdin 会把两个人半句话拼成一行")
    if dry_run:
        return ("--dry-run 产的是每一席的模型 prompt，真人那一席没有 prompt 可 dump："
                "转储里混进一席不由模型答的座位，数出来的就是假账。两条各跑一次")
    if not 1 <= seats[0] <= cfg.seat_count:
        return (f"--human 要在这桌的名册里（1-{cfg.seat_count}，共 {cfg.seat_count} 席），"
                f"收到 {seats[0]}：不在名册里的座位号会被当成『这局没有真人』")
    return None


def cmd_run(args: argparse.Namespace) -> int:
    if (err := _games_error(args.games)) is not None:
        print(f"配置错误：{err}", file=sys.stderr)
        return 2
    cfg = build_config()
    try:
        cfg = batch.apply_overrides(cfg, _run_overrides(args))
    except batch.BadOverride as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
    # 名册先于磁盘：`--set seat_count=5` 也是名册的一部分，所以这一句要等 `cfg` 定型之后，
    # 而 `out.mkdir` 还在它后面——命令写错不该留下一个目录（`#58` 给 `--games` 立的次序）。
    if (err := _human_error(cfg, args.human, dry_run=args.dry_run)) is not None:
        print(f"配置错误：{err}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = [args.seed + i for i in range(args.games)]

    if args.dry_run:
        return _cmd_dry_run(cfg, seeds, out)

    transport = None
    if not args.mock:
        from .transport import HttpTransport

        try:
            # Checked here, not in `chat()`: the transport only reads the env var when it
            # sends, so a missing key used to surface as an uncaught ConfigError from the
            # middle of the first night — after the log file had been created and one seat's
            # turn had already been thrown away.
            cfg.require_key()
            transport = HttpTransport(cfg)
        except ConfigError as e:
            print(f"配置错误：{e}", file=sys.stderr)
            return 2
    return _run_and_close(lambda: _run_many(cfg, seeds, out, args, transport), transport)


async def _run_many(cfg: Config, seeds: list[int], out: Path, args, transport) -> int:
    rc = 0
    human = args.human[0] if args.human else None
    for seed in seeds:
        actors = (make_actors(cfg, seed, mock=True, human=human) if transport is None
                  else _llm_actors(cfg, transport, human))
        res = await play(cfg=cfg, deal_seed=seed, out_dir=out, actors=actors)
        print(_summary_line(res), flush=True)
        if not args.quiet:
            for line in render_chronicle_file(res.path, god=args.god):
                print(line)
        if res.terminal not in metrics.DECISIVE | {DRAW_DAY_LIMIT}:
            # A draw is an answer; an outage is not. Returning 1 for both means a wrapper script
            # that retries on failure re-burns a batch of legal results, and plan §12 R10's
            # pre-registered adjudication reads as a crash.
            rc = 1
    return rc


def _llm_actors(cfg: Config, transport, human: int | None = None):
    """收 seed 的是发牌（`play(deal_seed=...)`）和 mock 座位的人格抽样（`make_actors`），不是这里。

    `human` 走的是与 `make_actors` 同一只手（`_roster`）：真桌留一席给打字的人这件事，本片只在
    `--mock` 桌上有用例，端点那一侧没有现场——所以 `#125` 之前不要把 `['human','llm']` 的局当数据。
    """
    from .actors import LlmActor
    from .llm import LLM
    llm = LLM(transport, cfg)
    return _roster(cfg, lambda s: LlmActor(llm, cfg, s), human=human)


def _summary_line(res: GameResult) -> str:
    return (f"[{res.game_id}] {res.terminal} winner={res.winner or '-'} day={res.days} "
            f"events={res.events} fallback={res.fallbacks} retry={res.retries} "
            f"timeout={res.timeouts} overflow={res.context_overflows} "
            f"completion={res.completion_tokens} {res.wallclock_s:.1f}s -> {res.path}")


# --------------------------------------------------------------------- one renderer
def render_chronicle_file(path: Path, *, god: bool = False, as_seat: int | None = None
                          ) -> list[str]:
    events, meta, torn = EventLog.read_split(path)
    lines = render_chronicle(events, god=god, as_seat=as_seat)
    if (stamp := render_html.provenance_line(meta)):
        lines.insert(0, f"〔{stamp}〕")
    # The 〔…〕 lines below are about the file, not the game: it ends here, or has no manifest, or
    # nothing after it, or a numbering that isn't 1,2,3 — four rules, one owner each in `events.py`.
    # The stamps head the file instead: 「哪一版写的」 is not something a tail notice gets to say.
    for notice in (roster_notice(meta), meta_notice(meta), empty_notice(events, meta),
                   seq_notice(events), torn_notice(torn)):
        if notice:
            lines.append(f"〔{notice}〕")
    return lines


def render_chronicle(events: list[Event], *, god: bool = False, as_seat: int | None = None,
                     show_fallback: bool = True) -> list[str]:
    """`as_seat` is not a filter for convenience — it *is* the isolation property made
    visible: give a colleague the same file with `--seat 3` and they watch a different game,
    on purpose."""
    if as_seat is not None:
        shown = percept_for(as_seat, events).events
    elif god:
        shown = events
    else:
        shown = [e for e in events if e.visibility == "all"]
    private = {e.seq for e in events if e.visibility != "all"}
    out = []
    for e in shown:
        line = render_line(e)
        if e.seq in private:
            line = f"{line}{PRIVATE_HINT}"
        if show_fallback and e.result.get("fallback"):
            line = f"{line}{FALLBACK_HINT}"
        out.append(line)
    return out


# --------------------------------------------------------------------------- dry run
def _cmd_dry_run(cfg: Config, seeds: list[int], out: Path) -> int:
    """Assemble every prompt a game would send; call nothing.

    The cheapest way to iterate on plan §5's budget table, so it is the everyday command
    rather than the exotic one. It plays a *mock* game to reach the states: day 1 alone never
    exercises the folding path, which is the code most likely to produce the HTTP 400 this
    exists to prevent.
    """
    per_phase: dict[str, list[int]] = {}
    over = 0
    total = 0
    ptok = ctok = 0
    for seed in seeds:
        actors = make_actors(cfg, seed, mock=True)
        res = asyncio.run(play(cfg=cfg, deal_seed=seed, out_dir=out, actors=actors))
        dump = out / f"g{seed:08d}.prompts.jsonl"
        dump.unlink(missing_ok=True)
        for actor in actors.values():
            for ctx in actor.turns:  # every prompt this seat was handed, attempts included
                p = ctx.prompt
                per_phase.setdefault(ctx.phase.value, []).append(p.total_tokens)
                over += int(p.over_ceiling)
                total += 1
                ptok += p.total_tokens
                ctok += cfg.token_budget_for(ctx.phase)
                with dump.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "seat": ctx.seat, "phase": ctx.phase.value, "attempt": ctx.attempt,
                        "total_tokens": p.total_tokens, "region_tokens": p.region_tokens,
                        "over_ceiling": p.over_ceiling, "shrink": p.shrink,
                        "messages": [{"role": m["role"], "content": m["content"]}
                                     for m in p.messages],
                    }, ensure_ascii=False) + "\n")
        print(f"[{res.game_id}] {res.terminal} day={res.days} prompts -> {dump.name}")
    usable = _print_census(cfg, per_phase, over, total, ptok, ctok, len(seeds))
    return 0 if usable else 1


def _print_census(cfg: Config, per_phase: dict[str, list[int]], over: int, total: int,
                  ptok: int, ctok: int, games: int) -> bool:
    """Print the census; return whether it is a conclusion (False = nothing was captured)."""
    print(f"\n== prompt 长度普查（{total} 次装配，零 API 调用；天花板 "
          f"{cfg.tokens.absolute_ceiling}，目标 {cfg.tokens.target}）==")
    if not per_phase:
        # An empty census is the failure mode worth naming: `turns` would be empty if the
        # agent ever handed an actor a prompt it never stored, and the run would look clean.
        print("没有捕获到任何 prompt —— 装配钩子失效了，这本身就是失败。")
        return False
    for phase in sorted(per_phase, key=lambda k: -max(per_phase[k])):
        s = sorted(per_phase[phase])
        p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
        print(f"  {phase:14s} n={len(s):4d} p50={int(mean(s)):5d} p95={p95:5d} max={max(s):5d}")
    print(f"  超天花板：{over}")
    # 墙钟 = 每局调用次数 × 每次的解码量。这两个乘数以前只有 plan §187 的一句估算在兜着，
    # 而它们今天是可以零请求量出来的：局数、prompt token、按 `token_budget_for` 计价的完成
    # 预算。剩下的未知量只有端点的吞吐与每次固定开销——那两格在 `docs/calibration.md`。
    print(f"  成本合计（{games} 局 · {total / max(games, 1):.1f} 次/局）：{total} 次调用 · "
          f"prompt {ptok} tok · 完成预算 {ctok} tok"
          f"（按 max_tokens 的上限算，非实测生成长度；每局硬顶 "
          f"{cfg.max_game_completion_tokens}）")
    return True


# ----------------------------------------------------------------------------- audit
def cmd_audit(args: argparse.Namespace) -> int:
    """One JSON object per game: the file's shape, then M2–M8 over it.

    Every rate here is `metrics.py`'s, not a second implementation of it. `audit` used to
    compute its own `fallback_rate` over *all events* while the batch report counts *decision
    turns* — two denominators, two numbers, one file. That is the same class of mistake as the
    `act`/`action` field-name trap m8 fell into, and the fix is the same: one function, pinned
    by a test.

    `--calibration` is the only way external data enters this object, and it is opt-in for that
    reason: the default output has to stay a function of the log, recomputable on a machine
    where `data/calibration.json` has a different life. When it is given, the file it read — and
    what it concluded about that file — is printed next to the number that depends on it.
    """
    g = metrics.read_game(Path(args.file))
    events, meta = g.events, g.meta
    cal = (metrics.load_calibration(args.calibration, expect_model=meta.get("model"))
           if args.calibration else None)
    asks = [e for e in events if e.request]
    kinds = Counter(e.kind for e in events)
    tokens = [int(e.request.get("total_tokens_est") or 0) for e in asks]
    folds = [int(e.request.get("compactions") or 0) for e in asks]
    # `None` when no request carries it, rather than 0: the field landed after the first batches
    # were written, and "no prompt sat on the day floor" is good news nobody measured there.
    floor = ([v for v in (e.request.get("b2_over_cap") for e in asks) if v is not None]
             or None)
    # One pass over the requests, shared with `wolf compare`'s per-arm table: the card cells below
    # are lifted out of it rather than recomputed here, because two readings of one field is how a
    # single-file verdict and an arm-level verdict start disagreeing.
    check = metrics.region_budget_check(g)
    flags = Counter(f.split(":")[0] for e in events for f in e.result.get("flags", ()))
    print(json.dumps({
        "meta": {k: meta.get(k) for k in ("game_id", "deal_seed", "config_hash", "actor_kinds", "model", "board",
                                          "reproducible", "contract_version", "rules_version", "compress_version")},
        "synthetic": g.is_synthetic,
        "events": len(events),
        "terminal": g.terminal,
        # A count, never the bytes: the cut line can be a wolf chat. `null` for a file that ends
        # on a complete record, so `1` here cannot be confused with "the normal case".
        "torn_tail": None if not g.torn_tail else g.torn_extent,
        # The same three counts `replay` puts in its sentence, as numbers: `events: 3` alone
        # reads as "this game had three events" when the file actually lost one in the middle.
        "seq_damage": seq_damage(events),
        "days": g.days,
        # `null` means the log carries no verdict, which is not the same claim as `false`. The
        # threshold sits in the same payload record, so a reader who wants to re-check this one
        # bit can; nothing here recomputes the fallback *count*, because `m3_gate` already owns
        # that number and a second copy of a rate is how two answers for one file get written.
        "degraded_game": g.degraded_game,
        "kinds": dict(kinds),
        "speech_acts": dict(Counter(str(e.payload.get("act")) for e in events
                                    if e.kind == Kind.SPEECH)),
        # The other half of that distribution: what the judge asked each seat to do, and how often
        # it got that. `speech_acts` alone reads as a behavioural verdict when it may be a property
        # of the assignment table, and plan §7's first defence is about the pair.
        "assignment": metrics.assignment_compliance(events),
        "soft_flags": dict(flags),
        "prompt_tokens_est": {"max": max(tokens, default=0),
                              "mean": int(mean(tokens)) if tokens else 0},
        # §5 的整段区域几何买的就是"端点复用前缀"，而这是链条上唯一说得出它有没有兑现的一格。
        # `reuse_ratio: null` 说的是这批日志里没有一次调用报过 `cached_tokens`，不是复用率为 0。
        "prefix_cache": metrics.prefix_cache_reuse(events),
        # Different quantities, deliberately not collapsed into one "compactions" number:
        # `max_rounds` is how many days the fold front advanced inside one prompt, `prompts_folded`
        # counts prompts sent with a folded chronicle, `events` counts the distinct fold states the
        # log has a `Kind.COMPACTION` marker for. Only the last one is a cache-flush ledger; a log
        # from before the marker existed reads 0 there while the other two are non-zero, which is
        # the honest difference between "never folded" and "folded, but nobody wrote it down".
        # The last two answer a different question: the day floor lets B2 finish folding without
        # meeting `regions.b2`, so `b2_prompts_on_the_floor` counts those prompts and
        # `b2_worst_over_tokens` how far the worst one went — both read the excess assemble wrote
        # per prompt, and `null` says the field had not been written yet, not that it was 0.
        # `last_fold` 是这一块里唯一读**标记 payload** 的格子：上面的个数说出现过几种折叠状态，这一格说最后那一种还剩几条逐字（抄 `metrics.last_fold_state`，不在此重算）。
        # The pair after them is the C side of the same problem: the shipped `region_tokens` is
        # measured *after* the card is thinned, so the only evidence that a prompt ever lost an
        # accusation line is the count assemble wrote at send time. Its unit is lines, not tokens.
        "compactions": {"max_rounds": max(folds, default=0),
                        "prompts_folded": sum(1 for x in folds if x),
                        "events": kinds.get(Kind.COMPACTION, 0),
                        "b2_prompts_on_the_floor": None if floor is None else sum(1 for x in floor if int(x) > 0),
                        "b2_worst_over_tokens": None if floor is None else max((int(x) for x in floor), default=0),
                        "card_prompts_thinned": check["card_prompts_thinned"],
                        "card_worst_claims_dropped": check["card_worst_claims_dropped"],
                        "last_fold": metrics.last_fold_state(events)},
        "region_budget_check": check,
        # 两份 `fallback` 拷贝对过账没有：算术在 `metrics`，这一格只是让它读得出来（`#119`）。
        "fallback_copy_check": metrics.fallback_copy_check(events),
        "m2_illegal": metrics.m2_illegal_rate(events),
        "m3_gate": metrics.m3_gate_pressure(events),
        "m4_hallucination": metrics.m4_hallucination_rates(events),
        "m5_style": metrics.m5_style_collapse(events),
        "m6_belief_action": metrics.m6_belief_action(events),
        **({"calibration": cal} if cal else {}),
        "m7_cost": metrics.m7_cost_profile(
            events,
            constants=cal["constants"] if cal and cal["usable"] else None,
            calibration_note=cal["note"] if cal and not cal["usable"] else None),
        "m8_strategy": metrics.m8_strategy_proxies(events),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    if (err := _seat_error(args.seat, Path(args.file))) is not None:
        print(f"配置错误：{err}", file=sys.stderr)
        return 2
    for line in render_chronicle_file(Path(args.file), god=args.god, as_seat=args.seat):
        print(line)
    return 0


# ------------------------------------------------------------------ export / watch
def cmd_export(args: argparse.Namespace) -> int:
    """One HTML file, written from the log alone. This is the artifact that leaves the laptop,
    so `--god` is opt-in: the default view is the one that is safe to send."""
    src = Path(args.file)
    out = Path(args.out) if args.out else src.with_suffix(".html")
    render_html.write_report(src, out, god=args.god)
    print(f"复盘 -> {out}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Tail a game as it is played, or `--once` for a frame in a script. `--seat` opens straight
    into one seat's head; `g` and the digits get there from the keyboard."""
    if (err := _seat_error(args.seat, Path(args.file))) is not None:
        print(f"配置错误：{err}", file=sys.stderr)
        return 2
    return render_live.watch(Path(args.file), god=args.god, reveal_seat=args.seat,
                             one_shot=args.once)


# ------------------------------------------------------------------------------- batch / compare
def _coerce(raw: str) -> Any:
    """`--set` arrives as text; the config field it lands on decides the reading.

    `apply_overrides` type-checks against the current value, so a wrong reading is an error
    message rather than a silently different arm — which is why "1" is not a bool here.
    """
    t = raw.strip()
    if t in ("true", "false"):
        return t == "true"
    if t.startswith("["):
        # Tuple-valued fields (`temperature_ladder`) are treatment variables too, and the
        # repro line prints them as JSON — so the reader has to accept what the writer wrote.
        return json.loads(t)
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            continue
    return t


def _parse_sets(pairs: list[str], names: list[str]) -> dict[str, dict[str, Any]]:
    """`--set B.temperature=0.6` → `{"B": {"temperature": 0.6}}`, refusing an unknown arm.

    The arm name is required on every pair: `--set temperature=0.6` would have to guess which
    arm to apply to, and a guess here is the difference between two arms and two batches.
    """
    per: dict[str, dict[str, Any]] = {n: {} for n in names}
    for pair in pairs:
        arm, _, rest = pair.partition(".")
        key, sep, raw = rest.partition("=")
        if not sep or arm not in per:
            raise batch.BadOverride(
                f"--set {pair}: 写成 <臂名>.<字段>=<值>，臂名要在 --configs 里列过（{('、'.join(names))}）")
        try:
            per[arm][key] = _coerce(raw)
        except ValueError as e:
            raise batch.BadOverride(f"--set {pair}: 值读不出来（{e}）") from e
    return per


def _canary_probe(cfg: Config, transport) -> "batch.ProbeFn":
    """The real endpoint's canary executor: five fixed prompts, temp 0, one call each.

    Kept out of `batch.py` so the batch layer stays testable with the endpoint offline — the
    abort-on-mismatch logic is worth more than a live probe would be if it can only be proven
    against a running server.
    """
    from .llm import EndpointUnavailable, LLM

    llm = LLM(transport, cfg)

    async def probe(prompt: str) -> dict[str, Any]:
        res = await llm.complete([{"role": "user", "content": prompt}],
                                 max_tokens=80, temperature=0.0, phase_key="canary")
        if not res.ok:
            raise EndpointUnavailable(res.error[:160])
        return {"answer": res.text.strip(), "latency_s": round(res.latency_s, 3),
                "completion_tokens": res.completion_tokens}

    return probe


def cmd_batch(args: argparse.Namespace) -> int:
    names = [n.strip() for n in args.configs.split(",") if n.strip()]
    if len(set(names)) != len(names) or not names:
        print("配置错误：--configs 要列出互不重名的臂，例如 --configs A,B", file=sys.stderr)
        return 2
    if (err := _games_error(args.games)) is not None:
        print(f"配置错误：{err}", file=sys.stderr)
        return 2
    base = build_config()
    arms: list[batch.Arm] = []
    try:
        per = _parse_sets(args.set, names)
        for name in names:
            try:
                arms.append(batch.Arm(name, batch.apply_overrides(base, per[name]),
                                      overrides=tuple(per[name])))
            except batch.BadOverride as e:
                # Name the arm: with two of them on the command line, "配置里没有 temp" does
                # not say whose typo it is, and that is the one thing the reader has to fix.
                raise batch.BadOverride(f"{name}: {e}") from e
    except batch.BadOverride as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    transport = canary = None
    if not args.mock:
        from .transport import HttpTransport

        try:
            base.require_key()  # before any game is played, same reason as cmd_run
            transport = HttpTransport(base)
            canary = _canary_probe(base, transport)
        except ConfigError as e:
            print(f"配置错误：{e}", file=sys.stderr)
            return 2
    try:
        res = _run_and_close(lambda: batch.run_batch(arms, games=args.games, seed0=args.seed0,
                                                     out_dir=Path(args.out), mock=args.mock,
                                                     transport=transport,
                                                     canary=batch.NO_CANARY if canary is None
                                                     else canary), transport)
    except batch.BatchAborted as e:
        print(f"批次中止：{e}", file=sys.stderr)
        return 1
    print(f"批次 -> {res.out_dir}（{res.n_logs} 局日志，seed0={res.pair_keys[0]}，"
          f"canary {res.canary.get('terminal')}，终态 {res.terminal}）；M3 闸门判定见 m3_gate.md")
    return 0 if res.terminal == "ok" else 1


def cmd_compare(args: argparse.Namespace) -> int:
    """Refuse or conclude, and write whichever of the two it is. Exit 0 only on a verdict of OK
    so a script can branch on it; a rejection is a report too, so it lands on disk as well.

    `--json` 是这批读数的机器侧出口。为什么落文件而不是像 `audit` 那样印到 stdout：`audit` 一次
    一局，stdout 就是它的产物；`compare` 的 stdout 已经被 markdown 占了，再接一段 JSON 等于让
    `wolf compare | jq` 拿到两条流。为什么 JSON 里不许有 `markdown`：那是同一批读数的第二种
    渲染，落两个地方，改口的时候只有一个是真的。
    """
    d = Path(args.dir)
    if not (d / "run_manifest.json").exists():
        print(f"配置错误：{d} 里没有 run_manifest.json，不是 wolf batch 产出的目录", file=sys.stderr)
        return 2
    axis = tuple(a.strip() for a in args.axis.split(",") if a.strip())
    out = batch.compare(d, axis=axis)
    dst = Path(args.out) if args.out else d / "comparison.md"
    dst.write_text(out["markdown"], encoding="utf-8")
    print(out["markdown"])
    if args.json:
        js = dst.with_suffix(".json")
        js.write_text(json.dumps({k: v for k, v in out.items() if k != "markdown"},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON -> {js}")
    print(f"{out['verdict']} -> {dst}")
    return 0 if out["verdict"] == "OK" else 1


def cmd_gate(args: argparse.Namespace) -> int:
    """对一个目录里**已经存在**的日志出 M3 判定：不打牌、不碰端点，只读盘。

    为什么要第三个入口：`compare` 进门就要 `run_manifest.json`，`batch` 要重新打牌才有臂，所以
    三次 `wolf run` 攒出来的一目录真日志，在链上任何地方都拿不到判定——而 §十四 验收第 3 条要的
    是"全部达标**或有明确失败记录**"。算术仍然只有 `metrics.m3_gate_verdict` 一份、阈值只有
    `M3_GATE` 一张，这里只是多一个读取点，与 `audit` 那格 `m3_gate_pressure` 分两层：单局只配
    出原始计数，判定得有一个分母（batch.py 里那段"per-game PASS 离 FAIL 只有一个离群点"）。

    两张配置表不并进一个分母：松散日志身上只剩 `meta.config_hash`，混在一起就说不清塌的是哪张桌
    子，所以这里拒判、点名，让人去分目录或回到 `batch` 的臂。一份连表都没登记的文件不算第二张表
    ——它没有 hash 可混，闸门把它单独报成"不是局的文件"（`#99`：以前它贡献一个 `None`，两局好日志
    因此一起被拒，还被说成"本批 3 局"）。
    """
    d = Path(args.dir)
    rows = batch.read_arm(d)
    if not rows:
        print(f"配置错误：{d} 里没有 *.jsonl，没有什么可判的", file=sys.stderr)
        return 2
    with_table = [r for r in rows if r.get("config_hash")]
    tables = sorted({str(r["config_hash"]) for r in with_table})
    if len(tables) > 1:
        no_table = (f"（另有 {len(rows) - len(with_table)} 份连配置表都没登记，没算进表数里）"
                    if len(rows) != len(with_table) else "")
        print(f"配置错误：{d} 里混着 {len(tables)} 张配置表（{'、'.join(tables)}）{no_table}："
              "M3 判的是「这一臂的桌子塌不塌缩」，两张表并成一个分母就说不清是谁塌的。"
              "分成两个目录各自判，或者用 `wolf batch` 的臂目录。", file=sys.stderr)
        return 2
    arm = f"cfg={tables[0]}" if tables else "cfg=未登记"
    out = batch.emit_gate(d, [arm], [{**r, "arm": arm} for r in rows], label="日志目录")
    v = out["verdicts"][arm]
    print(out["text"])
    print(f"{v['verdict']} -> {out['path']}")
    return 0 if v["verdict"] == "PASS" else 1


# ------------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    """The `wolf` surface, as an object.

    `main()` used to build it inline, which left the option sets uninspectable without spawning
    a process — so a doc telling a reader to type `--frobnicate` had nothing to check it against.
    `tests/test_doc_citations.py` reads this instead of running the CLI.
    """
    ap = argparse.ArgumentParser(prog="wolf", description="狼人杀多智能体博弈数据引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="打一局（--mock 不需要端点）")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--games", type=int, default=1)
    r.add_argument("--out", default="data")
    r.add_argument("--mock", action="store_true", help="用合成替身打牌，不碰端点")
    r.add_argument("--human", action="append", type=int, default=[], metavar="座位",
                   help="把这一席交给坐在终端前的人（只给一个；其余各席仍由替身或模型答）")
    r.add_argument("--dry-run", action="store_true", help="装配全部 prompt 并落盘，零 API 调用")
    r.add_argument("--god", action="store_true", help="时间线里显示私有事件")
    r.add_argument("--quiet", action="store_true", help="只打汇总行")
    r.add_argument("--set", action="append", default=[], metavar="字段=值", help="换一张预算表再装配，例如 --set regions.b2=400")
    r.add_argument("--max-days", type=int, default=None,
                   help="覆盖日数上限（默认取 Config.max_days）；打到上限即判平局 draw_day_limit")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("replay", help="复盘一个 JSONL（离线可演示）")
    p.add_argument("file")
    p.add_argument("--seat", type=int, default=None, help="只看这个座位能看到的")
    p.add_argument("--god", action="store_true", help="上帝视角")
    p.set_defaults(func=cmd_replay)

    a = sub.add_parser("audit", help="一局的质量与成本计数")
    a.add_argument("file")
    a.add_argument("--calibration", metavar="PATH", default=None,
                   help="读 scripts/calibrate.py 的 sidecar，把拟合常数喂给 M7 的 drift 自检；"
                        "不给就不碰文件系统（audit 的默认输出只是日志的函数）")
    a.set_defaults(func=cmd_audit)

    x = sub.add_parser("export", help="把一个 JSONL 导出成单文件复盘 HTML（离线）")
    x.add_argument("file")
    x.add_argument("-o", "--out", default=None, help="默认写在日志同名 .html")
    x.add_argument("--god", action="store_true", help="上帝视角：含私有事件与心里想")
    x.set_defaults(func=cmd_export)

    w = sub.add_parser("watch", help="直播一个 JSONL（只读，端点可关）")
    w.add_argument("file")
    w.add_argument("--god", action="store_true", help="以上帝视角开场（键盘 g 切换）")
    w.add_argument("--seat", type=int, default=None,
                   help="开局即看这个座位（键盘 1-9 直达、enter 逐席走、esc 收回）")
    w.add_argument("--once", action="store_true", help="只打一帧就退出（脚本/截图用）")
    w.set_defaults(func=cmd_watch)

    b = sub.add_parser("batch", help="N 局 × K 配置的可配对批次（plan §8 第 1 条）")
    b.add_argument("--configs", required=True, help="臂名，逗号分隔，例如 A,B")
    b.add_argument("--set", action="append", default=[], metavar="臂.字段=值",
                   help="某臂的覆盖项，可重复，例如 --set B.temperature=0.6")
    b.add_argument("--games", type=int, default=20, help="每臂几局（两臂共用同一批 deal_seed）")
    b.add_argument("--seed0", type=int, default=1000)
    b.add_argument("--out", required=True, help="批次目录，每个臂一个子目录")
    b.add_argument("--mock", action="store_true", help="合成桌：只证明管线，永不进结论")
    b.set_defaults(func=cmd_batch)

    c = sub.add_parser("compare", help="两臂配对检验；不合格就拒绝并写明原因")
    c.add_argument("dir", help="batch 产出的目录")
    c.add_argument("--axis", default="", help="声明的处理轴，逗号分隔，例如 temperature")
    c.add_argument("-o", "--out", default=None, help="默认写在批次目录的 comparison.md")
    c.add_argument("--json", action="store_true",
                   help="读数另写一份 JSON，落在 markdown 旁边同基名（comparison.json）")
    c.set_defaults(func=cmd_compare)

    gt = sub.add_parser("gate", help="对一目录已有日志出 M3 判定（离线，不打牌、不碰端点）")
    gt.add_argument("dir", help="装 `wolf run` 日志的目录，或批次里的一个臂目录")
    gt.set_defaults(func=cmd_gate)
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        return ns.func(ns)
    except LogDamage as e:
        # 本模块的 docstring 早把 2 留给"命令本身不对"，而 `events.py` 攒出这句话（哪个文件、第几
        # 行、缺哪些键）的意义就在这一行：让它穿过来越终端的人读到的是 traceback，那句话掉在最后。
        print(f"日志读不下去：{e}", file=sys.stderr)
        return 2
    except OSError as e:
        # 同一条腿的另一半：路径打错（FileNotFoundError）、把目录当日志（IsADirectoryError）、
        # 输出落不下（写侧的同一个类）此前都是 traceback + rc 1，而 1 在这里是"引擎拒绝"——
        # 一次拼错的文件名会被脚本读成"这批数据不可比"。`str(e)` 自带路径和 errno，不重抄。
        print(f"路径用不了：{e}", file=sys.stderr)
        return 2


def _run_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """`run` 那张桌子用的表：`--set` 的每一对，加上 `--max-days` 这个特例拼法。

    这两个函数住在 `main()` 下面是刻意的：文档按行号引本文件下游的语句，插入点每往上一格，
    那些号就要集体改一遍。两个拼法同时给则拒绝，不比"谁后写谁赢"：`--max-days 4 --set max_days=5`
    里赢的那个若是字典写入顺序决定的，那份普查按几天打就只有 argparse 的实现细节知道。
    """
    sets = _parse_run_set(args.set)
    if args.max_days is not None:
        if "max_days" in sets:
            raise batch.BadOverride(
                f"--max-days 与 --set max_days= 是同一根旋钮的两个拼法，这次同时给了 "
                f"{args.max_days} 和 {sets['max_days']}：留一个，普查才知道这桌按几天打")
        sets["max_days"] = args.max_days
    return sets


def _parse_run_set(pairs: list[str]) -> dict[str, Any]:
    """`run --set regions.b2=400` → `{"regions.b2": 400}`：一张桌，所以没有臂名前缀。

    只借 `_coerce` 的读法，不借判据——"这一格能不能改"仍然只由 `batch.apply_overrides` 说了算，
    所以 `run` 的普查和 `batch` 的臂用的是同一张 veto 名单（inert 格子在两侧都停在门口）。
    """
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise batch.BadOverride(f"--set {pair}: 写成 <字段>=<值>，例如 --set regions.b2=400")
        try:
            out[key.strip()] = _coerce(raw)
        except ValueError as e:
            raise batch.BadOverride(f"--set {pair}: 值读不出来（{e}）") from e
    return out


def _roster(cfg: Config, seat_actor, *, human: int | None):
    """把 `cfg.seat_count` 张椅子摆好，`human` 那一席交给打字的人。

    全仓库唯一一处构造 `HumanActor`（`tests/test_run_with_human.py` 在磁盘上数那个调用点）。这条
    判据存在的理由不是审美：座位表一旦有两处拼法，"这一席坐着人"就有了两个写者，而日志里
    `actor_kinds` 只有一份——两者不一致时读侧看到的仍是引擎说的那一份。

    住在文件尾是和 `_run_overrides` 同一个理由（文档按行号引本文件下游的语句），不是随手放的。
    """
    from .actors import HumanActor

    return {s: (HumanActor(s) if s == human else seat_actor(s))
            for s in range(1, cfg.seat_count + 1)}


if __name__ == "__main__":
    raise SystemExit(main())
