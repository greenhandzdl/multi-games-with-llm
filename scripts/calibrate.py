#!/usr/bin/env python3
"""M0 endpoint calibration. Produces docs/calibration.md.

Every latency constant used anywhere in this project must trace back to a row in that
file. Do not hard-code timings elsewhere.

Reads the key from the environment only (WOLF_LLM_API_KEY). Never writes it to disk,
never includes it in an exception message or log line.

    export WOLF_LLM_API_KEY=<key>
    python scripts/calibrate.py [--quick]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wolfengine.config import Config  # noqa: E402
from wolfengine.metrics import (  # noqa: E402
    CALIBRATION_KEYS as QUOTABLE_KEYS,
    collapse_round,
    declared_models,
    fitted_constant,
    mentions_seat,
    model_denial,
    opening_distinct_rate,
    template_top_fragments,
)
from wolfengine.transport import usage_from  # noqa: E402

CONF = Config()
KEY_ENV = CONF.api_key_env

# A filler sentence with a stable chars/token ratio, so budgets can be aimed precisely.
UNIT = "Villager Aldric walked to the market and bought bread cheese and rope. "


# Keys whose *value* is a credential. Deliberately narrow: matching the bare substring
# "token" elided half of this script's own findings (usage_keys_seen, has_cached_tokens),
# i.e. the guard destroyed the evidence it was guarding. Value-level scrubbing below is
# what actually protects the key.
CRED_KEYS = ("authorization", "api_key", "apikey", "x-api-key", "secret", "password", "credential")


def redact(obj):
    """Defence in depth: no auth material may ever reach a printed value."""
    if isinstance(obj, dict):
        return {
            k: ("<elided>" if any(t in str(k).lower() for t in CRED_KEYS) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        key = os.environ.get(KEY_ENV, "")
        return obj.replace(key, "<elided>") if key else obj
    return obj


def report_sections(features, stream, ratio, tp, lat, temps) -> dict:
    """The exact object the report prints, before redact touches it. Shared so the terminal
    warning and the §0 line can never disagree about what got blanked."""
    return {"features": features, "stream": stream, "ratio": ratio,
            "throughput": tp, "latency": lat, "temps": temps}


def elided_paths(obj, prefix: str = "") -> list[str]:
    """Where `redact` replaced a whole value with the mark, as dotted paths.

    Whole values only: an error string that had the key substring in it keeps its rest, so it
    still carries a finding. The case worth shouting about is a *result* that is now a mark —
    an early build blanked `has_cached_tokens` that way, and the report read as "the endpoint
    exposes nothing" instead of "our scrubbing ate the answer".
    """
    if isinstance(obj, dict):
        out: list[str] = []
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if v == "<elided>":
                out.append(path)
            else:
                out += elided_paths(v, path)
        return out
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in elided_paths(v, f"{prefix}[{i}]")]
    return []


def looks_like_gateway_error(body: str) -> bool:
    """The endpoint sits behind a proxy that returns HTML on upstream failure. Treating
    that as a 400-length problem (and compressing/retrying forever) would be a loop."""
    head = body[:400].lstrip().lower()
    return head.startswith("<") or "<html" in head or "upstream error" in head or "do request failed" in head


class Client:
    def __init__(self) -> None:
        self.key = os.environ.get(KEY_ENV)
        if not self.key:
            raise SystemExit(f"{KEY_ENV} not exported. export {KEY_ENV}=<key>")
        self.h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    async def complete(self, c: httpx.AsyncClient, msgs, *, mt=32, temp=0.0, extra=None, tries=3):
        body = {"model": CONF.model, "messages": msgs, "max_tokens": mt, "temperature": temp}
        body.update(extra or {})
        last: dict = {}
        for attempt in range(tries):
            t0 = time.perf_counter()
            try:
                r = await c.post(f"{CONF.base_url}/chat/completions", json=body, headers=self.h)
                dt = time.perf_counter() - t0
                if r.status_code != 200:
                    last = {"ok": False, "status": r.status_code, "err": redact(r.text[:200]), "dt": dt}
                    # 400 = the request is wrong; resending it is a loop. 5xx/gateway = the
                    # endpoint is unhealthy, which is exactly what a shared LAN box does under
                    # someone else's load, and is worth one backoff.
                    if not looks_like_gateway_error(r.text) and r.status_code < 500:
                        return last
                else:
                    o = r.json()
                    # One reader for "what did the endpoint report about this call's tokens":
                    # transport.usage_from() already owns the nested-branch tolerance, and the
                    # engine's prefix-cache reading comes from that same function.
                    u = usage_from(o.get("usage") or {})
                    ch = o["choices"][0]
                    return {
                        "ok": True,
                        "dt": dt,
                        "pt": u.get("prompt_tokens"),
                        "ct": u.get("completion_tokens"),
                        "cached": u.get("cached_tokens"),
                        "finish": ch.get("finish_reason"),
                        "text": ch["message"]["content"] or "",
                        "choice_keys": sorted(ch.keys()),
                    }
            except Exception as e:  # noqa: BLE001
                last = {"ok": False, "err": redact(f"{type(e).__name__}: {e}")[:200], "dt": time.perf_counter() - t0}
            if attempt < tries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
        last["attempts"] = tries
        return last


async def _probe(out: dict, name: str, fn) -> None:
    """Record a probe's failure as a row rather than aborting a ten-minute run."""
    try:
        out[name] = await fn()
    except Exception as e:  # noqa: BLE001
        out[name] = f"PROBE FAILED: {redact(f'{type(e).__name__}: {e}')[:160]}"


async def probe_features(c: httpx.AsyncClient, cl: Client) -> dict:
    """What does this server actually expose? Answers become table rows, not guesses."""
    out: dict = {}

    async def models():
        r = await c.get(f"{CONF.base_url}/models", headers=cl.h)
        if r.status_code != 200:
            return {"status": r.status_code, "body": r.text[:120]}
        j = r.json()
        m = (j.get("data") or [{}])[0]
        # A version/revision field is what makes R7 (endpoint swapped weights) detectable.
        has_version = any(k in m for k in ("revision", "version", "parent"))
        res = {
            "status": 200,
            "body": redact(j),
            "version_field_available": has_version,
            "model_fields": sorted(m.keys()),
        }
        if not has_version:
            res["version_debt"] = (
                "端点不提供模型版本/修订字段，因此'服务被人重启换了权重'只能靠 canary 探针间接发现，"
                "无法直接依据。这是口径债，不要假装有。"
            )
        return res

    await _probe(out, "models_endpoint", models)

    # /v1/tokenize would give exact counts. If absent, budgets run on the fitted ratio.
    async def tokenize(path):
        base = CONF.base_url.rsplit("/v1", 1)[0]
        r = await c.post(f"{base}{path}",
                         json={"model": CONF.model, "prompt": "hello world 你好"}, headers=cl.h)
        return r.status_code

    for path in ("/tokenize", "/v1/tokenize"):
        await _probe(out, f"tokenize{path}", lambda p=path: tokenize(p))

    # cached_tokens presence decides whether APC hit-rate is free to measure.
    async def apc():
        pre = UNIT * 600
        msgs = [{"role": "system", "content": pre}, {"role": "user", "content": "Say OK."}]
        await cl.complete(c, msgs, mt=8)
        r = await cl.complete(c, msgs, mt=8)
        return {"probe_ok": r["ok"], "has_cached_tokens": r.get("cached") is not None,
                "cached_value": r.get("cached")}

    await _probe(out, "apc_visibility", apc)

    async def usage_keys():
        r = await c.post(
            f"{CONF.base_url}/chat/completions",
            json={"model": CONF.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4},
            headers=cl.h,
        )
        return sorted((r.json().get("usage") or {}).keys()) if r.status_code == 200 else [f"HTTP {r.status_code}"]

    await _probe(out, "usage_keys_seen", usage_keys)

    # n>1 / response_format / stop / logprobs re-confirm against the current build.
    for name, extra in (
        ("n_gt_1", {"n": 2}),
        ("response_format_json", {"response_format": {"type": "json_object"}}),
    ):
        r = await cl.complete(
            c, [{"role": "user", "content": 'Reply {"a":1}'}], mt=24, extra=extra
        )
        out[name] = "supported" if r["ok"] else f"rejected ({r.get('status')})"

    # "HTTP 200" is not the question. The question is whether usable logprob numbers come
    # back — that decides whether a cheap confidence signal exists at all.
    rlp = await cl.complete(
        c, [{"role": "user", "content": "Say exactly: hello world"}], mt=16,
        extra={"logprobs": True, "top_logprobs": 3},
    )
    lp = (rlp.get("choice_keys") or [])
    out["logprobs"] = {"request_accepted": rlp["ok"], "choice_keys": lp,
                       "verdict": "returned" if "logprobs" in lp else "NOT returned (accepted then dropped)"}

    # Two-sided stop test. A one-sided "did the banned string appear" test passes when the
    # model simply never generates it; requiring the prefix AND the absence AND the
    # finish_reason makes a false green much harder.
    rs = await cl.complete(
        c, [{"role": "user", "content": "输出数字序列，用空格分隔：1 2 3 4 5 6 7 8 9 10 11 12"}],
        mt=60, extra={"stop": [" 8"]},
    )
    txt = rs.get("text", "")
    out["stop_honored"] = {
        "text": txt[:120],
        "has_7": "7" in txt,
        "has_9": "9" in txt,
        "finish": rs.get("finish"),
        "verdict": "honored" if (rs["ok"] and "7" in txt and "9" not in txt and rs.get("finish") == "stop")
                   else "NOT honored",
    }

    # seed determinism, the assumption a data engine most wants to be true
    same = 0
    outs = []
    for _ in range(3):
        r = await cl.complete(
            c,
            [{"role": "user", "content": "你是狼人杀3号玩家，身份狼人。用中文说两句白天发言。"}],
            mt=90, temp=0.0, extra={"seed": 42},
        )
        outs.append(r.get("text", "") if r["ok"] else "")
    same = len({o for o in outs if o})
    out["greedy_seed42_distinct_of_3"] = same
    out["determinism_verdict"] = (
        "deterministic" if same == 1 else "NON-deterministic (temp=0 + seed do not pin output)"
    )
    return out


async def measure_streaming(c: httpx.AsyncClient, cl: Client) -> dict:
    """Real streaming vs 'generated then chunked'. The UI is designed not to care,
    but the demo experience does, so record which it is with evidence."""
    body = {"model": CONF.model, "messages": [{"role": "user", "content": "从1数到30，用空格分隔。"}],
            "max_tokens": 120, "temperature": 0.0, "stream": True}
    stamps: list[float] = []
    t0 = time.perf_counter()
    try:
        async with c.stream("POST", f"{CONF.base_url}/chat/completions", json=body, headers=cl.h) as r:
            if r.status_code != 200:
                return {"supported": False, "status": r.status_code}
            async for line in r.aiter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    stamps.append(time.perf_counter() - t0)
    except Exception as e:  # noqa: BLE001
        return {"supported": False, "err": redact(str(e))[:150]}
    if len(stamps) < 2:
        return {"supported": True, "chunks": len(stamps), "verdict": "too few chunks to tell"}
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    ttft = stamps[0]
    span = stamps[-1] - stamps[0]
    med = statistics.median(gaps)
    implied_tps = len(stamps) / span if span > 0 else None
    # A threshold of 20ms mislabelled a genuinely streaming endpoint as buffered: chunks
    # arriving every ~17ms IS the decode rate (≈60 tok/s). The discriminator is the span
    # between first and last chunk, not the gap size — buffered flushes everything at once.
    if span < 0.15:
        verdict = "缓冲式（chunk 在末尾集中到达）"
    elif med > 0.004 and implied_tps and implied_tps > 10:
        verdict = f"真流式（chunk 到达率 {implied_tps:.0f}/s，与解码速率同级）"
    else:
        verdict = "无法判定"
    return {
        "supported": True, "chunks": len(stamps), "ttft_s": round(ttft, 3),
        "first_to_last_span_s": round(span, 3),
        "median_interchunk_s": round(med, 4),
        "implied_chunk_rate_tps": round(implied_tps, 1) if implied_tps else None,
        "verdict": verdict,
    }


async def measure_throughput(c: httpx.AsyncClient, cl: Client) -> dict:
    """The two constants the whole latency model rests on, measured serially.

    A short call and a long call on the same tiny prompt differ only by decoded tokens,
    so their gap is the per-call fixed cost; decode rate then follows from the long call.
    This matters more than it looks: if ~1s of every call is overhead that no concurrency
    removes, the lever is *fewer calls*, not smarter batching.
    """
    short = await cl.complete(c, [{"role": "user", "content": "回复 OK"}], mt=8)
    long_ = await cl.complete(
        c, [{"role": "user", "content": "写一段 500 字左右的中文说明，主题是夜间值班流程。"}], mt=600
    )
    out = {"short": None, "long": None}
    if short["ok"] and long_["ok"]:
        fixed = short["dt"] - (short["ct"] or 0) / 69.0
        d_raw = (long_["ct"] or 0) / long_["dt"]
        d_corr = (long_["ct"] or 0) / max(long_["dt"] - fixed, 1e-6)
        out = {
            "short": {"dt_s": round(short["dt"], 3), "pt": short["pt"], "ct": short["ct"]},
            "long": {"dt_s": round(long_["dt"], 3), "pt": long_["pt"], "ct": long_["ct"],
                     "finish": long_["finish"]},
            "per_call_fixed_overhead_s": round(fixed, 3),
            "decode_tps_raw": round(d_raw, 1),
            "decode_tps_overhead_corrected": round(d_corr, 1),
        }
    else:
        out["error"] = redact(f"short={short.get('status')} long={long_.get('status')}")
    return out


async def measure_ratio(c: httpx.AsyncClient, cl: Client) -> dict:
    """Fit tokens per unit so the budget tables can aim at a token count, not a guess."""
    pts, cts = [], []
    for reps in (50, 150, 300, 500):
        r = await cl.complete(
            c, [{"role": "system", "content": UNIT * reps}, {"role": "user", "content": "Say OK."}], mt=8
        )
        if r["ok"]:
            pts.append((reps * len(UNIT), r["pt"]))
            cts.append(r["pt"] / (reps * len(UNIT)))
    # CJK ratio matters more than English for this project's prompts.
    zh = "村民阿尔德里奇去集市买了面包奶酪和绳子。" * 200
    rz = await cl.complete(c, [{"role": "system", "content": zh}, {"role": "user", "content": "说OK。"}], mt=8)
    return {
        "en_tokens_per_char": round(statistics.median(cts), 4) if cts else None,
        "en_chars_per_sample": [p for p, _ in pts],
        "zh_pt": rz.get("pt"),
        "zh_tokens_per_char": round(rz["pt"] / len(zh), 4) if rz.get("ok") and rz.get("pt") else None,
        "unit_tokens_estimate": round(len(UNIT) * (statistics.median(cts) if cts else 0.25), 2),
        "fallback_rule_under_test": "ASCII/4 + CJK/1.6",
    }


async def latency_matrix(c: httpx.AsyncClient, cl: Client, quick: bool) -> list[dict]:
    """For each (k, prefix) cell: warm the shared prefix, then fire k concurrent calls.

    Warm-then-measure is the game's steady state (region A+B is byte-stable for a whole
    day). Cold is measured once per prefix size as the APC-off reference point.
    """
    import hashlib

    rows = []
    ks = [1, 4, 8] if quick else [1, 2, 4, 8, 9]
    prefixes = [50, 130] if quick else [35, 130, 270, 520, 800]
    for reps in prefixes:
        # unique tag defeats the persistent cache so each cell starts genuinely cold
        tag = hashlib.md5(f"{reps}{time.time()}".encode()).hexdigest()[:10]
        pre = UNIT * reps + f" [calib-{tag}]"
        cold = await cl.complete(c, [{"role": "system", "content": pre}, {"role": "user", "content": "Say OK."}], mt=8)
        warm = await cl.complete(c, [{"role": "system", "content": pre}, {"role": "user", "content": "Say OK."}], mt=8)
        for k in ks:
            sem = asyncio.Semaphore(k)

            async def one(i: int, sem=sem, pre=pre):
                async with sem:
                    return await cl.complete(
                        c,
                        [{"role": "system", "content": pre},
                         {"role": "user", "content": f"You are seat {i}. Give a 2-sentence Chinese day speech."}],
                        mt=CONF.max_tokens_speech, temp=0.9,
                    )

            t0 = time.perf_counter()
            res = await asyncio.gather(*(one(i) for i in range(k)))
            wall = time.perf_counter() - t0
            ok = [r for r in res if r["ok"]]
            bad = [r for r in res if not r["ok"]]
            if not ok:
                rows.append({"prefix_reps": reps, "k": k,
                             "error": f"status={bad[0].get('status')} {redact(str(bad[0].get('err')))[:140]}"})
                continue
            tot_ct = sum(r["ct"] or 0 for r in ok)
            tot_pt = sum(r["pt"] or 0 for r in ok)
            lats = sorted(r["dt"] for r in ok)
            rows.append({
                "prefix_reps": reps,
                "pt": ok[0]["pt"],
                "k": k,
                "cold_s": round(cold["dt"], 2) if cold["ok"] else None,
                "warm_s": round(warm["dt"], 2) if warm["ok"] else None,
                "apc_speedup": round(cold["dt"] / warm["dt"], 2) if cold["ok"] and warm["ok"] else None,
                "wall_s": round(wall, 2),
                "lat_p50_s": round(lats[len(lats) // 2], 2),
                "lat_max_s": round(lats[-1], 2),
                "agg_decode_tps": round(tot_ct / wall, 1) if wall else None,
                "agg_prefill_tps": round(tot_pt / wall, 1) if wall else None,
                "n_errors": k - len(ok),
                "err_sample": redact(str(bad[0].get("err"))[:140]) if bad else "",
            })
            print(f"  reps={reps:4} pt={ok[0]['pt']:6} k={k}: wall={wall:5.2f}s "
                  f"p50={lats[len(lats)//2]:5.2f}s max={lats[-1]:5.2f}s agg_ct/s={tot_ct/wall:5.1f}", flush=True)
    return rows


async def temp_diversity_scan(c: httpx.AsyncClient, cl: Client, quick: bool) -> list[dict]:
    """The R1 gate, measured before any game engine exists.

    Same predicament, N seats, varying temperature. Reports M5's own definitions so the
    numbers here are directly comparable to what the engine will later log.
    """
    rows = []
    temps = [0.8] if quick else [0.3, 0.7, 0.9, 1.1]
    for temp in temps:
        speeches, errs = [], []
        for _ in range(3):  # 3 replicated rounds, so template fragments need >=3 to count
            # Serial on purpose: this measures *content* diversity, and firing 9 at a shared
            # box that wedges under load would confound "model is passive" with "endpoint died".
            for i in range(9):
                r = await cl.complete(
                    c,
                    [{"role": "system", "content": f"你是狼人杀{i+1}号玩家。"},
                     {"role": "user", "content": "第一天白天，轮到你发言，说两句话。"}],
                    mt=CONF.max_tokens_speech, temp=temp,
                )
                if r["ok"]:
                    speeches.append(r["text"])
                else:
                    errs.append(r)
        if not speeches:
            first = errs[0] if errs else {}
            rows.append({"temperature": temp, "error":
                         f"all calls failed: status={first.get('status')} {redact(str(first.get('err'))[:140])}"})
            continue
        frags = template_top_fragments(speeches)
        collapse, openings = collapse_round(speeches), opening_distinct_rate(speeches)
        rows.append({
            "temperature": temp,
            "n_speech": len(speeches),
            # 一次成功的采样不是一次测量：两条尺子都要 ≥2 份发言才有可比对象，None 原样进表
            # （`n_speech` 就在旁边那一格，读表的人看得到为什么这一格是空的）。
            "collapse_round": None if collapse is None else round(collapse, 3),
            "opening_distinct_rate": None if openings is None else round(openings, 3),
            "seat_mention_rate": round(sum(mentions_seat(s) for s in speeches) / len(speeches), 3),
            "top_fragment": frags[0][0] if frags else None,
            "top_fragment_freq": frags[0][1] if frags else 0,
        })
        print(f"  temp={temp}: collapse={rows[-1]['collapse_round']} "
              f"openings={rows[-1]['opening_distinct_rate']} "
              f"mention={rows[-1]['seat_mention_rate']} top_frag={rows[-1]['top_fragment']!r}", flush=True)
    return rows


def derive_constants(lat: list[dict], tp: dict) -> dict:
    """Fit the two-term model. Rows that failed are counted, never skipped silently."""
    single = [r for r in lat if r.get("k") == 1 and r.get("wall_s")]
    gains = {}
    for k in (2, 4, 8, 9):
        cell = [r for r in lat if r.get("k") == k and r.get("wall_s") and r.get("prefix_reps") == 130]
        base = [r for r in lat if r.get("k") == 1 and r.get("prefix_reps") == 130 and r.get("wall_s")]
        if cell and base:
            # 1.0 would mean perfectly serial; k would mean perfectly parallel.
            gains[f"wall_gain_at_k{k}"] = round(k * base[0]["wall_s"] / cell[0]["wall_s"], 2)
    prefill = [r["agg_prefill_tps"] for r in lat if r.get("agg_prefill_tps")]
    return {
        "D_decode_tok_s": tp.get("decode_tps_overhead_corrected"),
        "per_call_fixed_overhead_s": tp.get("per_call_fixed_overhead_s"),
        "P_prefill_tok_s_best_observed": max(prefill) if prefill else None,
        "concurrency_wall_gain": gains,
        "batching_verdict": (
            "并发几乎不给吞吐（见 concurrency_wall_gain），所以墙钟预算按 Σ单发时间 估，"
            "优化方向是减少调用次数与 completion 长度，不是提高并发。"
        ),
        "model": "T_call ≈ fixed_overhead + pt/P + ct/D ；T_phase ≈ Σ T_call（并发上限≈1）",
        "source_rows_ok": len(single),
        "rows_failed": len([r for r in lat if "error" in r]),
    }


def sidecar(ran_utc: str, quick: bool, features: dict, stream: dict, ratio: dict,
            tp: dict, lat: list, temps: list, *, model: str, base_url: str) -> dict:
    """The machine-readable half of a run: the raw sections *plus* the fitted constants block.

    `constants` lives in here because a reader in `src/` must not re-derive it — the fit needs
    the whole latency matrix, and the same quantity computed in two places is how the report and
    `wolf audit` start disagreeing about one run. `model` is stamped for the same reason the
    report's header carries it: a constant fitted on other weights is not a stale number, it is
    the wrong number, and only the reader can tell which.

    `base_url` is stamped for that same reason and was missing: the header line is a statement
    about *the measurement*, so a re-render that read it from live config would re-sign old
    numbers with whatever address the box has today.

    `model_declared` is the endpoint's own listing, kept beside the model we *asked* for. The
    requested name comes from config and would match config forever, so on its own it can never
    contradict anything — including R7, a box restarted on other weights. The listing is the one
    field in here that can.
    """
    return {"ran_utc": ran_utc, "quick": quick, "model": model, "base_url": base_url,
            "model_declared": declared_models(features),
            "features": features, "stream": stream, "ratio": ratio,
            "throughput": tp, "latency": lat, "temps": temps,
            "constants": derive_constants(lat, tp)}


TWIN_PROMISE = ("- 机器可读的孪生件：`data/calibration.json` 的 `constants` 块（`wolf audit "
                "--calibration <该文件>` 读它）。本文件负责说清这份体检**完不完整**，取值以孪生件为准；"
                "两处由同一次运行、同一个 `derive_constants()` 产生。\n")


def twin_line(src: str) -> str:
    """页眉那条"孪生件"的话只能说 sidecar 里真有的东西。

    在册的这一份 `data/calibration.json` 顶层只有 features/stream/ratio/latency/temps——`constants`
    块是后来才有的字段，所以它承诺的那一侧今天读到的是拒绝，而拒绝的话本来就有作者：
    `metrics.load_calibration()`。这里不重写它，只把它那句搬进页面（`#153` 同一条规矩：同一个理由
    在两处各自组装，迟早一处说"缺 D"、另一处说"文件不存在"）。

    note 的开头是"哪个文件"，被 `；` 之后的子句才是"为什么"。整条 note 里嵌着调用方给的**路径**，
    而这一页说的是仓库里那一份，所以取子句、不取整句——否则一次 `--from-json` 用相对路径、
    守卫测试用绝对路径，报告就会因为写法分叉。
    """
    from wolfengine.metrics import load_calibration  # 不进模块级导入：那会把 `CRED_KEYS` 顶到 54，归档里有一处按 53 点它
    verdict = load_calibration(src)
    if verdict["usable"]:
        return TWIN_PROMISE
    note = verdict["note"]
    clause = note.split("；", 1)[1] if "；" in note else note
    return (f"- 机器可读的孪生件：`data/calibration.json` **今天读不出常数**——{clause}。"
            "`wolf audit --calibration <该文件>` 打印的就是 loader 这一句，本页与它一起等下一次体检："
            "补不出常数时，两边都不许被当成来源。\n")


def render_md(features, stream, ratio, tp, lat, temps, args, *, provenance: str = "",
              base_url: str | None = None, model: str | None = None,
              twin: str) -> str:
    def table(rows, cols):
        head = "| " + " | ".join(cols) + " |\n"
        head += "|" + "|".join(["---"] * len(cols)) + "|\n"
        body = "".join(
            "| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |\n"
            for r in rows
        )
        return head + body

    failed = [r for r in lat if "error" in r] + [r for r in temps if "error" in r]

    def stamp(value: str | None, live: str) -> str:
        # This line is a statement about *the measurement*. A sidecar written before the field
        # existed has no provenance to show, and silently filling the gap from live config would
        # re-sign old numbers with whatever address or weights the box carries today. The config
        # is still worth pointing a reader at — as long as the line says that is where it came from.
        return f"`{value}`" if value else f"未记录（当前配置为 `{live}`，不是本次测量的出处）"

    constants = derive_constants(lat, tp)
    # The three numbers other code is allowed to quote. If one was not measured, saying so in
    # the header is the whole difference between "unmeasured" and "measured as unavailable":
    # the last run's file read as an authority while its decode constant was a column average
    # over ~100-token completions, i.e. prefill and queueing, not throughput.
    #
    # `fitted_constant` is `load_calibration`'s own ruler, imported rather than restated: the
    # page header and the sidecar are two readings of one run, and `is None` was the looser of
    # the two. A zero-latency server is a legal OpenAI-compatible server, and it fits
    # `per_call_fixed_overhead_s` to a negative — which `is None` called a measurement.
    missing = [k for k in QUOTABLE_KEYS if not fitted_constant(constants.get(k))]
    # R7 ("somebody restarted the box behind us on other weights") is otherwise only discoverable
    # through the canary probe, i.e. indirectly. Both halves of this comparison already sit in §1's
    # JSON; a claim that lives only in prose is a defect, so the report puts them on one line.
    # Gated on `model` being a *recorded* value, not a config fallback — comparing the live config
    # against a past measurement would be a second instance of the bug this block detects.
    #
    # `model_denial` is `load_calibration`'s own clause, imported rather than restated: this page
    # and that refusal are one sentence written twice, and the last time that happened a negative
    # overhead passed one ruler and not the other.
    denial = model_denial(model, declared_models(features))
    refusals = ([f"{'、'.join(missing)} 没有可用的值（未测得或不为正）"] if missing else []) + \
               ([denial] if denial else [])
    # The same redact() that keeps the key out of the file can eat a *finding*. Say which
    # cells it did, so nobody reads a blank the guard made as an answer the endpoint gave.
    blind = elided_paths(redact(report_sections(features, stream, ratio, tp, lat, temps)))
    L = ["# 端点体检结果（全工程唯一合法的延迟常数来源）\n",
         f"- 生成命令：`export {KEY_ENV}=<key> && python scripts/calibrate.py"
         f"{' --quick' if args.quick else ''}`",
         f"- base_url：{stamp(base_url, CONF.base_url)}  model：{stamp(model, CONF.model)}",
         "- 密钥仅从环境变量读取，本文件不含其值。\n",
         twin]
    if provenance:
        L.append(f"- 数据来源：{provenance}\n")
    if refusals:
        L.append("> **本节尚不构成常数来源**：" + "；".join(refusals) +
                 "。见第 0 节。补齐之前，引用这些位置的报告一律标『未标定』，"
                 "不得回填计划里的估计值。\n")
    L += [
         "## 0. 体检完整性\n",
         f"- 延迟矩阵失败单元格：**{len([r for r in lat if 'error' in r])}**；温度扫描失败档位："
         f"**{len([r for r in temps if 'error' in r])}**",
         "- 失败单元格保留在下表并注明原因。**未测得即为未知，不得用计划里的估计值冒充实测值。**\n"]
    # The clause above is `load_calibration`'s refusal verbatim, so a page that claims to be a
    # source and a reader that gets refused can no longer be two different facts.
    if denial:
        L.append(f"- **Model 对账**：{denial}。每个请求体里写的都是页眉那个名字，而这份清单是端点"
                 "自己给的——引用前先确认寻址；本节所有数字因此标为出处存疑。\n")
    if blind:
        L.append(f"- **另有 {len(blind)} 处的值被 redact 抹去**：{'、'.join(blind[:10])}"
                 f"{'…' if len(blind) > 10 else ''}。这些格子是防护逻辑遮掉了结论，**不是**"
                 "端点没返回；要么把结论换成不含凭据键名的形式记录，要么在本节写明该结论不可得。\n")
        # Same guard, later state: the bare `token` substring is gone, so a re-run is expected to
        # read these cells back. "Expected" is a reading of the code above, not a measurement —
        # this report is an offline re-render of an older sidecar, and saying otherwise would be
        # exactly the "claim that lives only in prose" this repo keeps hunting.
        L.append("  - 上面那句 remedy 的后半已经做了：`CRED_KEYS` 里不再有裸 `token`（就是它当年把 "
                 "`has_cached_tokens`、`usage_keys_seen` 一起遮掉的），被遮的格子也由 `elided_paths` "
                 "逐格报出来而不是静默消失。**下一次 M0 重跑预期能把这些格读回来**——这是从代码读出的"
                 "预期，不是本次重跑实测（本报告是旧 sidecar 的离线重渲染）。\n")
    if failed:
        def cell(r: dict) -> str:
            # Upstream error bodies contain newlines and pipes; either one silently destroys
            # the row it sits in, which is the opposite of why this table exists.
            return str(r["error"]).replace("|", "\\|").replace("\n", " ")[:150]

        L += ["| 项 | 失败原因 |", "|---|---|"]
        L += [f"| {r.get('prefix_reps', r.get('temperature'))} / k={r.get('k', '-')} | {cell(r)} |"
              for r in failed]
        L.append("")
    L += [
         "## 1. API 支持面与确定性\n",
         "```json",
         json.dumps(redact(features), ensure_ascii=False, indent=2),
         "```\n",
         "## 2. 流式判定\n",
         "```json",
         json.dumps(stream, ensure_ascii=False, indent=2),
         "```\n",
         "## 3. 单发吞吐与固定开销\n",
         "`per_call_fixed_overhead_s` 是并发拿不掉的那部分，通常决定一局的下限。\n",
         "```json",
         json.dumps(tp if tp else {"decode_tps": None,
                                   "note": "未采集：这次运行没有做单发吞吐测量"},
                    ensure_ascii=False, indent=2),
         "```\n",
         "## 4. chars→tokens 回归\n",
         "```json",
         json.dumps(ratio, ensure_ascii=False, indent=2),
         "```\n",
         "## 5. 并发 × 前缀 延迟矩阵\n",
         "`cold`/`warm` = 该前缀首次与该前缀命中缓存的单发耗时；`wall` = k 个并发请求的总墙钟。\n",
         table(lat, ["prefix_reps", "pt", "k", "cold_s", "warm_s", "apc_speedup",
                     "wall_s", "lat_p50_s", "lat_max_s", "agg_decode_tps", "agg_prefill_tps",
                     "n_errors", "err_sample"]),
         "## 6. 温度 × 多样性扫描（R1 塌陷基线）\n",
         "指标定义与 `metrics.py` 完全一致，因此这里的数字可直接与日后对局日志对比。\n"
         "注意 `opening_distinct_rate` 高但 `seat_mention_rate` 低 = 措辞多样而行为被动，"
         "正是 M3 把 `passivity_rate` 定为主判据的原因。\n",
         table(temps, ["temperature", "n_speech", "collapse_round", "opening_distinct_rate",
                       "seat_mention_rate", "top_fragment", "top_fragment_freq"]),
         "## 7. 拟合出的契约常数\n",
         "```json",
         json.dumps(constants, ensure_ascii=False, indent=2),
         "```\n",
         ]
    # Every entry is a line, not a fragment: `"".join` used to weld the bullets and the header
    # rows together, which rendered as one paragraph and made the failure table unreadable.
    return "".join(l if l.endswith("\n") else l + "\n" for l in L)


def render_from_json(src: str, out: str) -> None:
    """Re-render the report from a sidecar, without touching the endpoint.

    A header fix should not require the shared box to be free for twenty minutes — and the run
    whose report most needed the completeness header was precisely one that failed halfway
    through, which is also the only run with a sidecar on disk.
    """
    path = Path(src)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("ran_utc"):
        provenance = f"实测于 {data['ran_utc']}，本报告离线重渲染自 `{src}`"
    else:
        # The sidecar predates the timestamp field, so mtime is the only evidence of when the
        # numbers were taken. Stated as evidence about the file, not about the run.
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                              time.gmtime(path.stat().st_mtime))
        provenance = (f"采集时间未记入 sidecar（早于该字段），唯一可依据的是文件 mtime "
                      f"{stamp}；本报告离线重渲染自 `{src}`")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        render_md(data.get("features", {}), data.get("stream", {}), data.get("ratio", {}),
                  data.get("throughput", {}), data.get("latency", []), data.get("temps", []),
                  argparse.Namespace(quick=data.get("quick", False)), provenance=provenance,
                  base_url=data.get("base_url"), model=data.get("model"),
                  twin=twin_line(src)),
        encoding="utf-8")
    print(f"re-rendered {out} from {src}（零 API 调用）")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="smallest matrix that still yields constants")
    ap.add_argument("--out", default="docs/calibration.md")
    ap.add_argument("--json", default="data/calibration.json")
    ap.add_argument("--from-json", metavar="SRC", default=None,
                    help="离线重渲染报告：读上一次的 sidecar，零 API 调用")
    args = ap.parse_args()

    if args.from_json:
        render_from_json(args.from_json, args.out)
        return

    cl = Client()
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0), limits=limits) as c:
        # Preflight: one cheap call. The last run hammered the endpoint with 12k-token
        # prefixes first and every later probe failed, which reads as "the model is
        # passive" when it is really "the shared box was busy". Fail loudly instead.
        pre = await cl.complete(c, [{"role": "user", "content": "回复 OK"}], mt=8, tries=2)
        if not pre["ok"]:
            raise SystemExit(
                f"端点预检失败，未做体检（避免把'服务不可用'记成'模型行为'）："
                f"status={pre.get('status')} err={redact(str(pre.get('err')))[:160]}"
            )
        print(f"preflight ok: {pre['dt']:.2f}s pt={pre['pt']} ct={pre['ct']}", flush=True)

        print("[1/6] feature & determinism probes", flush=True)
        features = await probe_features(c, cl)
        print("[2/6] streaming", flush=True)
        stream = await measure_streaming(c, cl)
        print("[3/6] token ratio + single-stream throughput", flush=True)
        ratio = await measure_ratio(c, cl)
        tp = await measure_throughput(c, cl)
        print("[4/6] temperature/diversity scan (the R1 gate — runs before the load test)", flush=True)
        temps = await temp_diversity_scan(c, cl, args.quick)
        print("[5/6] latency matrix (heaviest; last, so it cannot poison the rest)", flush=True)
        lat = await latency_matrix(c, cl, args.quick)

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # 先落 sidecar 再渲染：页眉那条"孪生件"的话要从那份文件读回来，live 与 `--from-json` 才是同一个作者。
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(
        json.dumps(redact(sidecar(stamp, args.quick, features, stream, ratio, tp, lat, temps,
                                  model=CONF.model, base_url=CONF.base_url)),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Stamped into both halves here, not left to `render_md`'s reading of live config: the
    # round-trip test compares this header against the one a later `--from-json` rebuilds from
    # the sidecar, so if the two writers ever disagree about which endpoint was measured, that
    # test goes red instead of the report quietly gaining a second, unwitnessed source.
    md = render_md(features, stream, ratio, tp, lat, temps, args, provenance=f"实测于 {stamp}",
                   model=CONF.model, base_url=CONF.base_url, twin=twin_line(args.json))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    blind = elided_paths(redact(report_sections(features, stream, ratio, tp, lat, temps)))
    if blind:
        # Twenty minutes of a shared box is expensive to repeat; the run still counts, but the
        # operator has to see the blanked cells now rather than in the file three days later.
        print(f"!! redact 抹去了 {len(blind)} 处结论：{'、'.join(blind[:10])}"
              f"{'…' if len(blind) > 10 else ''}（详见 {args.out} §0）", file=sys.stderr, flush=True)
    print(f"\nwrote {args.out} and {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
