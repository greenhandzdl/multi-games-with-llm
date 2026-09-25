"""Action protocol and the parse/repair ladder.

The endpoint has no `response_format`, ignores `stop`, and returns prose after the object,
so "make it JSON" is client-side work. The ladder exists so each acceptance is *attributed*
(`repair_rung` is logged), which is how M3a answers the only question that matters: does
strict JSON hold up once the prompt is 5k tokens instead of my 200-token probe?

 rung0 strict: first balanced top-level object, escapes respected, trailing prose dropped
 rung1 repair:  fences, full-width structural punctuation, trailing commas, unbalanced braces
 rung2 coerce: "3号"/"三号"/"P3"->3, 枚举近义归一, "是/否"->bool
 rung3 validate: pydantic -> structured errors consumed by the retry prompt
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ActName = Literal[
    "kill", "save", "poison", "check", "vote", "shoot",
    "accuse", "defend", "align", "probe", "pivot", "listen", "pass", "discuss", "last_words",
]

# Only punctuation that can sit *outside* a string literal is translated. Converting a
# full-width comma inside 发言 would silently rewrite what the model chose to say.
STRUCTURAL_WIDTH = {
    "｛": "{", "｝": "}", "［": "[", "］": "]", "，": ",", "：": ":",
    "【": "[", "】": "]", "＂": '"', "、": ",",
}


class Suspect(BaseModel):
    model_config = ConfigDict(extra="ignore")
    seat: int = Field(ge=1, le=24)
    why: str = ""


class Belief(BaseModel):
    """Structured belief, not chain-of-thought.

    Declared in the same object as the action, which is what makes belief→action
    consistency (M6) a measurement instead of an interpretation — and a wolf's plan is
    expressed as the gap between this and `speech`, so nothing free-form needs storing.
    """

    model_config = ConfigDict(extra="ignore")
    suspects: list[Suspect] = Field(default_factory=list)
    read: dict[int, Literal["wolf", "good", "unknown"]] = Field(default_factory=dict)


class Action(BaseModel):
    model_config = ConfigDict(extra="ignore")
    act: ActName
    target: int | None = Field(default=None, ge=1, le=24)
    speech: str = ""
    evidence: list[str] = Field(default_factory=list)
    belief: Belief | None = None
    # night-only, witch: which potion she means when act=save/poison
    potion: Literal["save", "poison"] | None = None


ACT_SYNONYMS = {
    "怀疑": "accuse", "指控": "accuse", "抨击": "accuse", "指责": "accuse", "票": "vote",
    "投票": "vote", "出局": "accuse", "自证": "defend", "辩解": "defend", "辩护": "defend",
    "跟票": "align", "附议": "align", "质疑": "probe", "试探": "probe", "改口": "pivot",
    "转向": "pivot", "听": "listen", "观望": "listen", "弃票": "pass", "过": "pass",
    "跳过": "pass", "刀": "kill", "击杀": "kill", "验": "check", "查验": "check",
    "救": "save", "毒": "poison", "开枪": "shoot", "发言": "last_words", "讨论": "discuss",
}

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def coerce_seat(value: Any) -> int | None:
    """"3号" / "三号" / "P3" / 3 / "3" -> 3. Returns None if unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 1 else None
    s = str(value).strip()
    m = re.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    for cn, n in _CN_NUM.items():
        if cn in s:
            return n
    return None


VALID_ACTS = frozenset(a for a in ACT_SYNONYMS.values())


def normalize_act(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v in VALID_ACTS:
        return v
    stripped = v.strip(" 。.！!\"'")
    if stripped in ACT_SYNONYMS:
        return ACT_SYNONYMS[stripped]
    for zh, en in ACT_SYNONYMS.items():
        if zh in v:
            return en
    return None


# --------------------------------------------------------------------- rung 0 / 1


def extract_first_object(text: str) -> tuple[str | None, str]:
    """Scanner, not regex: `stop` is not honoured, so the model continues past `}` and the
    scanner's refusal to look further is what truncates it. Returns (object, remainder)."""
    start = text.find("{")
    if start < 0:
        return None, text
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], text[i + 1 :]
    return None, text[start:]  # unterminated -> caller closes braces and retries


def strip_structural_width(text: str) -> str:
    """Full-width→half-width, but only outside string literals."""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        else:
            out.append(STRUCTURAL_WIDTH.get(ch, ch))
    return "".join(out)


def repair(text: str) -> str:
    """Pre-scanner normalisation: fences and character-class damage, never structure."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```:?\s*$", "", t)
    t = re.sub(r",\s*([}\]])", r"\1", t)                      # trailing commas
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    t = strip_structural_width(t)
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    return t


@dataclass
class ParseOutcome:
    action: Action | None = None
    rung: int = -1                 # -1 = total failure
    errors: list[dict[str, Any]] = field(default_factory=list)
    raw_used: str = ""
    # What the model did wrong, independent of which rung saved it. This is what M3a
    # counts: the rung answers "did the parser cope", the deviations answer "was the
    # contract honoured". A markdown fence is absorbed by the scanner at rung 0, so
    # reporting only rungs would quietly call a contract violation "clean".
    deviations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.action is not None


def _deviations_of(text: str) -> list[str]:
    """Cheap, order-stable flags computed from the raw string before any repair."""
    devs: list[str] = []
    stripped = text.strip()
    if stripped.startswith("```"):
        devs.append("fence")
    if stripped.startswith("\n") or stripped.startswith("  "):
        devs.append("leading_whitespace")
    if any(c in text for c in STRUCTURAL_WIDTH):
        devs.append("full_width_punctuation")
    if any(c in text for c in ("“", "”", "‘", "’")):
        devs.append("smart_quotes")
    obj, rest = extract_first_object(text)
    if obj is None:
        devs.append("truncated")
    elif rest.strip():
        devs.append("trailing_text")
    return devs


def salvage_candidates(text: str) -> list[str]:
    """Candidate reconstructions of a JSON object the server cut off mid-token.

    `max_tokens` is 140 and this model talks past it, so truncation is the expected
    failure, not the exotic one. Two shapes must both work: a cut string (`"speech":"我跟`)
    and a cut key (`...,"tar`), and the second can only be saved by dropping back to the
    last complete pair — which is why the scan records those positions as it goes.

    Brace depth is tracked string-aware rather than counted: a `}` inside 发言 would fool
    a naive `.count()`.
    """
    start = text.find("{")
    if start < 0:
        return []
    depth = 0
    in_str = False
    esc = False
    cuts: list[int] = []  # top-level commas: everything before one is a complete prefix
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return [text[start : i + 1]]  # already complete
        elif ch == "," and depth == 1:
            cuts.append(i)
    out: list[str] = []
    body = text[start:]
    if in_str:
        bare = body[:-1] if body.endswith("\\") else body
        out.append(bare + '"' + "}" * max(depth, 1))
    out.append(body.rstrip().rstrip(",") + "}" * max(depth, 1))
    for cut in reversed(cuts):
        out.append(text[start:cut] + "}")
    return out


def parse_action(text: str) -> ParseOutcome:
    """Runs the ladder, and records both which rung accepted and what the model did wrong."""
    if not text or not text.strip():
        return ParseOutcome(errors=[{"loc": (), "msg": "empty response"}])

    devs = _deviations_of(text)
    obj, _rest = extract_first_object(text)
    fixed = repair(text)
    obj2, _ = extract_first_object(fixed)
    tried: list[tuple[int, str]] = []
    if obj is not None:
        tried.append((0, obj))
    if obj2 is not None and obj2 != obj:
        tried.append((1, obj2))
    tried += [(1, c) for c in salvage_candidates(fixed)]

    seen: set[str] = set()
    last = ParseOutcome(errors=[{"loc": (), "msg": "no object passed validation"}])
    for rung, cand in tried:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        coerced, hit = _coerce_fields(data)
        if hit:
            devs.append("coerced_field")
        uniq = tuple(dict.fromkeys(devs))
        try:
            return ParseOutcome(action=Action.model_validate(coerced), rung=rung,
                                raw_used=cand, deviations=uniq)
        except ValidationError as e:
            # Keep climbing. Returning here would abandon the ladder at the first
            # malformed candidate, and a truncated object *fails validation* far more
            # often than it fails to parse — which is exactly when salvage_candidates
            # exists. The rung-0 error text is still what the retry prompt should quote,
            # so the last one is kept for the outcome.
            last = ParseOutcome(
                errors=[{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()],
                raw_used=cand, deviations=uniq,
            )
            continue
    if tried:
        return last
    return ParseOutcome(errors=[{"loc": (), "msg": "no JSON object found"}],
                        deviations=tuple(dict.fromkeys(devs)))


def _coerce_fields(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """rung2: seat-ish values to ints, act synonyms to enum members."""
    hit = False
    out = dict(data)
    if "act" in out:
        if (n := normalize_act(out["act"])) and n != out["act"]:
            out["act"] = n
            hit = True
    for key in ("target", "vote", "kill", "check", "poison_target"):
        if key in out and not isinstance(out[key], int):
            if (c := coerce_seat(out[key])) is not None:
                out[key] = c
                hit = True
    if isinstance(out.get("belief"), dict):
        b = out["belief"]
        if isinstance(b.get("suspects"), list):
            fixed = []
            for item in b["suspects"]:
                if isinstance(item, dict):
                    item = dict(item)
                    if "seat" in item and not isinstance(item["seat"], int):
                        if (c := coerce_seat(item["seat"])) is not None:
                            item["seat"] = c
                            hit = True
                    fixed.append(item)
                elif isinstance(item, (int, str)):
                    if (c := coerce_seat(item)) is not None:
                        fixed.append({"seat": c})
                        hit = True
            b["suspects"] = fixed
        out["belief"] = b
    if isinstance(out.get("evidence"), str):
        out["evidence"] = re.findall(r"e\d+", out["evidence"])
        hit = True
    return out, hit


def errors_to_prompt_lines(errors: list[dict[str, Any]], legal_acts: tuple[str, ...],
                           targets: list[int]) -> str:
    """Retry text for region C5. Concrete and short, because a rejection that the model
    cannot act on just burns another 140 completion tokens."""
    lines = ["上一轮输出被引擎拒绝，原因："]
    for e in errors[:3]:
        loc = ".".join(str(p) for p in e.get("loc", ())) or "整体"
        lines.append(f"- {loc}: {e.get('msg', '')}")
    lines.append(f"act 只能是：{'/'.join(legal_acts)}")
    lines.append(f"target 只能是：{targets}")
    lines.append('只输出一个 JSON 对象，不要任何解释文字。')
    return "\n".join(lines)
