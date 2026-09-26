"""密钥边界：值不落盘，而且这条主张本身要有可执行的证据。

约定是"配置里只出现环境变量名"。写在 README 里的那句话不构成保证——保证来自：

* 任何会被序列化的 dict 里没有 `sk-` 形状、没有 24 位以上高熵串；
* 写盘的 `request` / `response` 走白名单，`headers` 连键名都不存在；
* `data/`（真轨迹含完整 prompt）和 `.env` 在 gitignore 里。

范围在这里钉死（`src/` + `tests/fixtures/` + `docs/` + `README.md` + 一份新生成的日志），因为一条"扫全仓
库"的命令在没 `git init` 的目录上会返回 0 且不报错——那是典型的假绿。想查提交历史请用
README 里那条 `git log -S`，并且**只在 `git init` 之后**执行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wolfengine.config import Config
from wolfengine.events import EventLog, Kind

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "src", ROOT / "tests" / "fixtures", ROOT / "docs", ROOT / "README.md"]
SK_SHAPED = re.compile(r"sk-[A-Za-z0-9]{8,}")
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9_\-+/=]{28,}\b")
# 白名单而非黑名单：允许出现的是我们自己造的长串（折叠摘要里的分隔线、fixture 文本），
# 它们一旦变成新的形状就该被看见，所以这里只列**具体**的、能一眼看完的例外。
BENIGN = re.compile(r"^[<-]*$|_{30,}|-{30,}|={30,}")


def _text_files() -> list[Path]:
    out: list[Path] = []
    for base in SCAN:
        if base.is_dir():
            out += [p for p in base.rglob("*")
                    if p.is_file() and p.suffix in {".py", ".md", ".json", ".jsonl", ".txt", ".toml", ".html"}]
        elif base.is_file():
            out.append(base)
    return out


def test_scan_roots_exist():
    """A guard whose target vanished passes silently. This one does not."""
    assert any(b.is_dir() for b in SCAN), "no scan roots — the test below would be vacuous"


def test_the_documented_paste_target_is_inside_the_scan():
    """`SCAN` 是目录清单，于是"哪一类文件没有读者"这件事就藏在它的形状里：`src/`、
    `tests/fixtures/`、`docs/` 三个目录，而 **README.md 作为一个裸文件路径不在其中**。这份
    仓库里最容易长出一段终端粘贴的文件恰恰是 README——真跑一次 `batch --real` 之后，粘进
    文档的就是那一段 stderr，里面带着 `Authorization: Bearer …`。上一跑数过：README 5678 行、
    全仓最长，而密钥扫描一行都不读它。

    断言只钉"路径进没进扫描集"，不钉"现在有没有密钥形状"：后者现在当然是绿的，绿得正好
    说明它没在扫。
    """
    names = {p.relative_to(ROOT).as_posix() for p in _text_files()}
    assert "README.md" in names, (
        f"README 不在密钥扫描里，而它是文档中最长的一份粘贴目标（扫到 {len(names)} 个文件，"
        f"没有一个叫 README.md）")


def test_no_key_shaped_string_anywhere_in_the_project():
    offenders: list[str] = []
    for path in _text_files():
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if SK_SHAPED.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n} sk-shaped")
    assert not offenders, "possible key value committed: " + "; ".join(offenders)


def test_no_high_entropy_token_in_serialisable_config():
    """Config 的序列化结果会进 manifest，因此它就是"值泄漏"最可能发生的地方。"""
    blob = json.dumps(Config().to_dict(), ensure_ascii=False, sort_keys=True, default=list)
    assert not SK_SHAPED.search(blob), "Config serialises something key-shaped"
    hits = [t for t in HIGH_ENTROPY.findall(blob) if not BENIGN.match(t)]
    assert not hits, f"unexpected high-entropy value in Config: {hits}"
    assert "api_key_env" in blob and "WOLF_LLM_API_KEY" in blob, "the NAME must still be there"


def test_config_hash_is_a_hash_not_a_dump():
    h = Config().config_hash()
    assert re.fullmatch(r"[0-9a-f]{12}", h)


@pytest.mark.parametrize("field", ["headers", "authorization", "api_key", "x-api-key"])
def test_the_log_whitelist_has_no_place_for_headers(field):
    """`payload_for_log` lists what is written. Asserting the *absence* of a key that has no
    branch anywhere is the difference between a whitelist and a hopeful blacklist."""
    from wolfengine.assemble import Prompt, payload_for_log

    written = json.dumps(payload_for_log(Prompt(messages=[])), ensure_ascii=False)
    assert field not in written.lower(), f"{field} made it into the request record"


def test_redact_elides_values_by_key_name_and_by_literal_content(tmp_path, monkeypatch):
    """Real bytes on disk, not a hypothetical dict.

    Two separate jobs, because there are two leaks: `redact` replaces a value sitting under
    a credential-shaped *key*, and `_scrub_string` removes a value that appears **inside** an
    exception message — the realistic one, since httpx echoes the request in
    `RemoteProtocolError` and that text is what lands in `attempts[].failure`.
    """
    from wolfengine.transport import redact

    sentinel = "sk-TESTONLY-0123456789abcdef"
    monkeypatch.setenv("WOLF_LLM_API_KEY", sentinel)

    out = redact({"headers": {"Authorization": f"Bearer {sentinel}"},
                  "error": f"POST failed with header Authorization: Bearer {sentinel}",
                  "usage": {"prompt_tokens": 10}})
    blob = json.dumps(out, ensure_ascii=False)
    assert sentinel not in blob, "credential survived redact (value or in-string)"
    assert "<elided>" in blob and out["usage"]["prompt_tokens"] == 10, \
        "redact over-removed: it must cost the value, not the diagnostics"

    log = EventLog(tmp_path / "g.jsonl")
    log.append(Kind.GAME_START, day=1, phase="night_wolf", seats=[1, 2, 3],
               request={"messages": [{"role": "system", "content": "规则"}]},
               response=out)
    assert sentinel not in (tmp_path / "g.jsonl").read_text(encoding="utf-8")


def test_gitignore_covers_traces_and_env():
    lines = {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for needed in ("data/", ".env"):
        assert needed in lines or needed.rstrip("/") in lines, f".gitignore missing {needed}"
    assert (ROOT / ".env.example").exists(), "the example file is how a key stays out of config"
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "API_KEY" in line and "=" in line:
            assert not line.split("=", 1)[1].strip(), f"{line!r} carries a value"
    assert not SK_SHAPED.search(example)
    # base_url 是局域网地址、模型 id 是公开字符串，两者按约定**可以**入库（plan §11）。
    # 入库的地方是 `config.py` 的字段默认值，不是样例文件：样例里只许出现真有人读的变量名，
    # 见下面 `test_every_variable_the_example_advertises_has_a_reader`。
    assert "http://100.87.65.60:13000/v1" in (ROOT / "src" / "wolfengine" / "config.py").read_text(
        encoding="utf-8"), "端点该以字面量躺在 config.py 里（非密、且不许当处理轴）"


# --------------------------------------------------- 样例不许宣传没有读者的变量

ENV_ADVERTISED = re.compile(r"^([A-Z][A-Z0-9_]{3,})\s*=")
ENV_READ_AT_CALL = re.compile(r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]{3,})["']""")
ENV_NAME_FIELD = re.compile(r"""\b\w*env\w*\s*:\s*str\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']""")


def _code_that_touches_the_environment() -> list[Path]:
    out: list[Path] = []
    for base in (ROOT / "src", ROOT / "scripts"):
        out += [p for p in base.rglob("*.py") if "os.environ" in p.read_text(encoding="utf-8")]
    return out


def _env_names_the_code_reads() -> set[str]:
    """代码真的拿去当配置用的环境变量名。

    两种写法算"读了"：调用点上的字面量（`os.environ.get("X")`），和一个名字里带 `env` 的
    `str` 字段的默认值（`api_key_env: str = "X"`，取用它的是 `require_key` 里那句
    `os.environ.get(self.api_key_env)`——间接，但确实是配置）。

    KNOWN_LIMIT：经过一个变量传进去的名字不收。`transport.py` 的清洗表
    （`for name in ("WOLF_LLM_API_KEY", "MVP_VLM_API_KEY")`）擦的是**值**，不是配置，
    所以它不该给样例发广告权；哪天它变成配置读法，就得改写成调用点字面量才会被这条看见。
    """
    names: set[str] = set()
    for path in _code_that_touches_the_environment():
        text = path.read_text(encoding="utf-8")
        names |= set(ENV_READ_AT_CALL.findall(text))
        names |= set(ENV_NAME_FIELD.findall(text))
    return names


def test_every_variable_the_example_advertises_has_a_reader():
    """.env.example 是一页"你可以去 export 这些"的广告——每一条都要有代码真的去取。

    动因是量出来的一件事：样例里 `WOLF_LLM_BASE_URL` / `WOLF_LLM_MODEL` 两行在 `src/` 里
    0 个读者（端点与模型是 `config.py` 的字段默认值，且同坐 `FORBIDDEN_AXIS`：CLI 既没有
    旗标也拒 `--set`），照手册去 export 的人得到的是静默无效。上一条 138 行原本反过来钉着
    "样例里必须有 BASE_URL 那行"，把这句假话钉成了规矩——那条断言已经改成钉"端点字面量在
    `config.py` 里"，广告这一侧由这条管。
    """
    advertised = {m.group(1) for line in (ROOT / ".env.example").read_text(
        encoding="utf-8").splitlines() if (m := ENV_ADVERTISED.match(line))}
    assert advertised, "样例里一个变量都没有，那这条就是在空集上自证"
    unread = sorted(advertised - _env_names_the_code_reads())
    assert not unread, f"这些变量只有样例在宣传、代码里没人读：{unread}"


def test_the_env_reader_corpus_actually_finds_the_key_name():
    """正控制：语料塌成空集时，上一条会对着空例子发合格证。

    `api_key_env` 走的是字段默认值那一支，`WOLF_LLM_API_KEY` 在调用点上从来不是字面量，
    所以这一条单独钉得住"扫描还接在 `config.py` 上"。
    """
    reads = _env_names_the_code_reads()
    assert "WOLF_LLM_API_KEY" in reads, f"扫到的只有 {sorted(reads)}——语料或正则坏了"
    assert (ROOT / ".env.example").read_text(encoding="utf-8").count("WOLF_LLM_API_KEY=") == 1
