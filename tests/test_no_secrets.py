"""密钥边界：值不落盘，而且这条主张本身要有可执行的证据。

约定是"配置里只出现环境变量名"。写在 README 里的那句话不构成保证——保证来自：

* 任何会被序列化的 dict 里没有 `sk-` 形状、没有 28 位以上的高熵串；
* 写盘的 `request` / `response` 走白名单，`headers` 连键名都不存在；
* `data/`（真轨迹含完整 prompt）和 `.env` 在 gitignore 里；
* 提交历史的新增行里没有密钥的值形状——这条是 `git log -p --all` 真跑出来的，不是留给人的作业。

范围在这里钉死（`src/` + `tests/fixtures/` + `docs/` + `README.md` + 一份新生成的日志），因为一条"扫全仓
库"的命令在没 `git init` 的目录上会返回 0 且不报错——那是典型的假绿。历史那一侧同一件事由
`_committed_history_patch()` 管：git 不答话、或答出来是空的，都直接报错，而不是让"零命中"冒充干净；
历史被截断（`git clone --depth`、CI 的默认深度）时它跳过并点名自己答不出——那一版上"从没加过"和
"看不见加过"是同一句话（`#190`）。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import git_history_is_shallow
from wolfengine.config import Config
from wolfengine.events import EventLog, Kind

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "src", ROOT / "tests" / "fixtures", ROOT / "docs", ROOT / "README.md"]
SK_SHAPED = re.compile(r"sk-[A-Za-z0-9]{8,}")
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9_\-+/=]{28,}\b")
# 白名单而非黑名单：允许出现的是我们自己造的长串（折叠摘要里的分隔线、fixture 文本），
# 它们一旦变成新的形状就该被看见，所以这里只列**具体**的、能一眼看完的例外。
BENIGN = re.compile(r"^[<-]*$|_{30,}|-{30,}|={30,}")
# 自己埋的哨兵：脱敏那两条用例拿它当"值"，历史扫描必须认得出它（一处定义，两边共用）。
SENTINEL = "sk-TESTONLY-0123456789abcdef"


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

    sentinel = SENTINEL
    monkeypatch.setenv(Config().api_key_env, sentinel)

    out = redact({"headers": {"Authorization": f"Bearer {sentinel}"},
                  "error": f"POST failed with header Authorization: Bearer {sentinel}",
                  "usage": {"prompt_tokens": 10}}, Config().api_key_env)
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

    KNOWN_LIMIT：经过一个变量传进去的名字不收。`transport.py` 擦的是**值**，取值的写法是
    `os.environ.get(key_env)`，名字由调用点交进来（擦值那两本的出厂名默认值已被 `#317`／`#318` 摘掉，
    所以这条看不见它——`WOLF_LLM_API_KEY` 仍然是靠 `config.py` 那个带 `env` 的字段默认值被记成
    有读者的。以前清洗表里还躺着第二个名字，它既没人配置也没人擦，是这一族的第一次；现在名单
    只剩一处来源，这条限界的含义也跟着变了：样例的广告权只可能来自配置，不可能来自清洗表。
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


# --------------------------------------------------- 提交历史：新增行里不许出现密钥的值形状

KEY_ASSIGN = re.compile(r"\b([A-Z][A-Z0-9_]*API_KEY[A-Z0-9_]*)\s*=\s*(\S*)")


def _added_lines(patch: str) -> list[tuple[str, str]]:
    """`git log -p` 文本里的新增行，连同它们属于哪份文件。

    只数 `+` 不数 `+++`，也不数上下文行：历史里被删掉的那一行不是泄漏，而 diff 头里的文件名
    永远进不了形状判断。真实历史上量过：全文扫高熵串会得 2908 处命中（`index abc..def` 那类
    40 位哈希），只看新增行仍剩 2166 处（文档里记录的 sha256、`uv.lock` 的完整性哈希），所以
    这条判据用的是 `sk-` 形状加"名字=长值"，不是 `HIGH_ENTROPY`。
    """
    out: list[tuple[str, str]] = []
    current = ""
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            current = line.split(" b/")[-1]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((current, line[1:]))
    return out


def _history_offenders(patch: str, *, exempt: tuple[str, ...] = (SENTINEL,)) -> list[str]:
    hits: list[str] = []
    for name, text in _added_lines(patch):
        for m in SK_SHAPED.finditer(text):
            if not any(m.group(0) in e for e in exempt):
                hits.append(f"{name}: 新增行里有 `sk-` 形状的值 {m.group(0)!r}")
        for m in KEY_ASSIGN.finditer(text):
            value = m.group(2)
            if value and not value.startswith("<") and len(value) >= 16:
                hits.append(f"{name}: {m.group(1)}= 后面跟着一个不像占位符的长值")
    return hits


def _require_full_history(repo: Path) -> None:
    """历史被截断时停笔：这一格查的是"哪一次提交加过、后来又删掉"，一棵树的快照答不出这句话。"""
    if git_history_is_shallow(repo):
        pytest.skip("这份克隆的 git 历史被截断了：`git log -p --all` 只看得见留下的那几条提交，"
                    "被删掉的那一行不在里面（depth-1 时它只剩一条，把整棵当前树当成「全部是新加的」）。"
                    "「提交历史里从没加入过 key 值」这句在这一版上没有对象可查，它今天没有读数："
                    "`git fetch --unshallow` 之后重跑才有。")


def _committed_history_patch() -> str:
    _require_full_history(ROOT)
    r = subprocess.run(["git", "log", "-p", "--all"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"扫不了提交历史，这条判据就成了假绿：{r.stderr.strip()[:200]}"
    assert r.stdout.strip(), "历史是空的——「没有任何值」这句话就没有对象可查"
    return r.stdout


def test_the_committed_history_added_no_key_value():
    """手册让人"查提交历史里有没有混进过 key"，那这两条命令就得真的有人跑——每次跑测试都跑。

    动因是 `#145`：README 从 `aa0d646` 起写着"本仓库目前**还没有任何提交**，这两条要等第一次
    `git commit` 之后才有对象可查"。那句话写下来的时候是真的（README 先于 `git init` 存在），
    而它死于它所在的那一次提交——此后 24 个提交、以及公开到 `origin/main` 的那一份，都把它
    当现行说明在发。假话不是因为有人查错了，而是因为查历史这件事一直只有散文、没有读者。
    """
    assert not _history_offenders(_committed_history_patch())


def test_the_history_scan_is_not_reading_an_empty_corpus():
    """正控制：不豁免时，同一个判据必须在真实历史里抓到东西——抓到的只能是我们自己埋的哨兵。

    没有这一条，上一条的"零命中"就分不清"历史干净"和"扫描根本没读到新增行"（`_added_lines`
    少一层判断、`git log -p` 的参数写错、豁免表被放宽成"任何 `sk-` 都算自己人"，三种都长得一样）。
    """
    lines = _added_lines(_committed_history_patch())
    assert len(lines) > 20_000, f"只读到 {len(lines)} 行新增，扫描面大概已经塌了"
    hits = _history_offenders(_committed_history_patch(), exempt=())
    assert hits, "一处形状都没抓到，那上一条就是在空集上发合格证"
    stray = [h for h in hits if "tests/test_no_secrets.py" not in h]
    assert not stray, f"哨兵之外的命中，上一条会红却没说清是谁：{stray}"


def test_the_history_scanner_names_a_planted_leak():
    """判据要能点名：拿一份合成的补丁喂它，它必须报出文件名，也必须放过删除行与占位符。

    为什么用合成补丁而不是真历史——真历史现在是干净的，干净状态下唯一的红证据来自"埋一根假的
    进去"，而那不能往真历史里埋（提交删不掉，而且 origin 是公开的）。所以判据写成纯函数：给一段
    `git log -p` 的文本，报出新增行里值形状的东西。往真历史里埋的活儿由下一条正控制接手，它查的是
    "扫描还读得到东西"，不是"扫描会报警"。
    """
    planted = "\n".join([
        "diff --git a/README.md b/README.md",
        "--- a/README.md",
        "+++ b/README.md",
        "@@",
        "-export WOLF_LLM_API_KEY=<key>",
        f"+export WOLF_LLM_API_KEY={SENTINEL}",
        " ",
    ])
    # 这一行故意拿哨兵自己当被泄漏的值：`sk-` 形状那一半被默认豁免挡着，抓住它的是"名字=长值"
    # 那一半——两条判据各有分工，夹具要分别钉住，否则摘掉任何一半都还是全绿。
    offenders = _history_offenders(planted)
    assert offenders, "判据对着一行明写的赋值报不出东西，那它对真实历史也报不出"
    assert any("README.md" in o for o in offenders), f"没点名是哪份文件：{offenders}"
    # 同一份补丁里，删除行和长占位符都不算数：判据只看新增行，而 `<…>` 是手册的占位写法
    # （写成 22 个字符是为了让"豁免占位符"那一半真的有东西可挡——短占位符连长度门槛都过不了）。
    assert not _history_offenders("\n".join([
        "diff --git a/docs/x.md b/docs/x.md", "+++ b/docs/x.md", "@@",
        f"-泄漏过又被删掉的那一行：WOLF_LLM_API_KEY={SENTINEL}",
        "+还是占位符：WOLF_LLM_API_KEY=<REPLACE_WITH_YOUR_KEY>",
    ]))


def test_the_truncation_probe_reads_both_shapes(full_and_shallow_clone):
    """探针两向都判得出来，而"截断"的症状是删除行为 0——行数的地板看不见它。

    动因是 `#189` 之后在公开克隆上重跑：在 `e04ea3d` 上拿 `git clone --depth 1` 出来的仓库里
    `git log -p --all` 有 44103 行新增、**0 行删除**（同一版本的完整历史是 50970 行新增、6865 行
    删除、82 个提交）。那一个提交把整棵当前树当成"全部是新加的"报出来，于是三条历史判据全绿，
    而它们查的命题——"有没有哪次提交加过 key 值、后来又删掉"——在这个克隆里根本没有对象。
    旁边那条 20 000 行的地板拦不住：快照比 82 个提交加起来的新增行只少一点点。
    所以这里钉的是形状而不是数量：新增行多不等于读到了历史。
    """
    full, shallow = full_and_shallow_clone
    assert not git_history_is_shallow(full), "两串提交的仓库被判成了浅克隆——它会去跳开一条本该开火的判据"
    assert git_history_is_shallow(shallow), "depth-1 克隆没被判成浅克隆，下面那条跳过永远不会开火"

    def corpus(repo):
        out = subprocess.run(["git", "log", "-p", "--all"], cwd=repo, capture_output=True, text=True)
        lines = out.stdout.splitlines()
        return (len(_added_lines(out.stdout)),
                sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---")))

    added_full, removed_full = corpus(full)
    added_shallow, removed_shallow = corpus(shallow)
    assert removed_full > 0, f"连现造的两次提交都读不出删除行，`git log -p` 这一形已经坏了：{corpus(full)}"
    assert removed_shallow == 0, "浅克隆里读到了删除行——下面那格断言的'症状'就不再成立"
    assert added_shallow >= 3, f"浅克隆的新增行太少，撑不起「行数地板拦不住它」这句：{added_shallow}"


def test_the_history_gate_refuses_to_sign_a_truncated_corpus(full_and_shallow_clone, monkeypatch):
    """历史判据在环境答不出时要停笔：跳过真的发生，且只在截断时发生。

    为什么拿 monkeypatch 而不是本仓库——本机带整段完整历史，这一支在这里永远走不到，
    而"一支没有读者的跳过"正是 `#153` 那一课（一具永远跳过的刀能无声杀掉一整格）。所以两向都在
    现造的仓库上各测一次，真实克隆里的那一次读数另外记进归档（`#189` 的做法：`git clone --depth 1`
    重跑，看这两条报的是 skipped 而不是 passed）。
    """
    full, shallow = full_and_shallow_clone
    _require_full_history(full)                         # 不跳：完整历史在这里答得出
    with pytest.raises(pytest.skip.Exception):
        _require_full_history(shallow)                  # 截断 → 停笔
    monkeypatch.setattr("test_no_secrets.git_history_is_shallow", lambda repo: True)
    with pytest.raises(pytest.skip.Exception):
        _committed_history_patch()                      # 判据确实接在探针上，不只是探针自己会判


def test_the_env_reader_corpus_actually_finds_the_key_name():
    """正控制：语料塌成空集时，上一条会对着空例子发合格证。

    `api_key_env` 走的是字段默认值那一支，`WOLF_LLM_API_KEY` 在调用点上从来不是字面量，
    所以这一条单独钉得住"扫描还接在 `config.py` 上"。
    """
    reads = _env_names_the_code_reads()
    assert "WOLF_LLM_API_KEY" in reads, f"扫到的只有 {sorted(reads)}——语料或正则坏了"
    assert (ROOT / ".env.example").read_text(encoding="utf-8").count("WOLF_LLM_API_KEY=") == 1


# ------------------------------------------- 取哪个变量：这个名字只许有一个作者

KEY_NAME_FACES = {"src": 20, "scripts": 1, "tests": 40}


def _writes_the_key_name_by_hand(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == name


def _hand_written_key_name_sites(src: str, rel: str) -> list[str]:
    """把出厂密钥变量名**当取值依据手写**的地方——四种形状：绑给一个名字、当实参交给一次调用
    （`setenv` 那一类）、当键写进一份字典、当键写进一次下标赋值。

    「句子里提到了这个名字」不算，因为分界是结构给的而不是名单给的：一条断言的内容就是名字本身
    （报错信息里该不该出现它），抄一遍是对的；而"我该往哪个变量里放东西"这件事只能有一个作者，
    也就是 `Config` 的那个字段默认值。作者那一格自己落在类体里，被下面那条断言点名，不进豁免名单。

    四种形状里没有一种是凑数的：`#313` 量过的三份抄本分属"绑给名""交给调用点""写进字典"，
    下标那一形现语料 0 枚，由合成证人钉着（下面那条控制用例）——它的失效方向是尺比形状窄，
    那种错永远不红。
    """
    name = Config().api_key_env
    out: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _writes_the_key_name_by_hand(node.value, name):
                out.append(f"{rel}:{node.lineno} 绑给 "
                           f"{','.join(ast.unparse(t) for t in targets)}")
        elif isinstance(node, ast.Call):
            if any(_writes_the_key_name_by_hand(a, name) for a in node.args):
                out.append(f"{rel}:{node.lineno} 交给 {ast.unparse(node.func)}()")
        elif isinstance(node, ast.Dict):
            out += [f"{rel}:{node.lineno} 写进字典"
                    for k in node.keys if _writes_the_key_name_by_hand(k, name)]
        elif isinstance(node, ast.Subscript):
            if _writes_the_key_name_by_hand(node.slice, name):
                out.append(f"{rel}:{node.lineno} 写进下标")
    return out


def test_the_factory_key_name_has_exactly_one_author():
    """`#147` 把擦值的那一步接上了 `api_key_env`，却没管**谁在往变量里放东西**。

    取值的写法（`os.environ.get`）不止一处——`#319` 那趟 AST 普查在 `config.py`、`transport.py`、`scripts/calibrate.py` 各量到一枚，名字都从配置来，改名以后它们跟着走；可测试侧要是不
    手抄一遍名字去 `setenv`，改了名的人就会看到"值没被擦掉"这种红，而真正坏的是那句对账。
    这条把"手抄"收成像，并要求剩下的唯一一处就是作者本人。

    语料地板按面各钉一格，因为三面的形状本来就不同：`src` 二十来本模块、`tests` 五十来本用例、
    `scripts` 现量只有一本体检程序——那一格钉的是"这个目录还在被扫"，不是"它该有很多本"。
    """
    sites: list[str] = []
    for face, floor in KEY_NAME_FACES.items():
        books = [p for p in (ROOT / face).rglob("*.py") if "__pycache__" not in p.parts]
        assert len(books) >= floor, (
            f"{face} 面只扫到 {len(books)} 本（地板 {floor}）：这条会在塌掉的语料上发合格证")
        for book in sorted(books):
            sites += _hand_written_key_name_sites(book.read_text(encoding="utf-8"),
                                                  str(book.relative_to(ROOT)))
    assert len(sites) == 1, f"出厂名被手写的地方应只剩作者，实测 {len(sites)} 处：{sorted(sites)}"
    assert sites[0].startswith("src/wolfengine/config.py") and "绑给 api_key_env" in sites[0], sites[0]


def test_the_key_name_probe_fires_on_each_shape_and_stays_off_prose():
    """四形各有一具合成证人，再加两形"不该收"的负控制。

    正控制不靠仓库里恰好有几枚抄本：仓库会被修干净，而尺比形状窄这种错在修干净之后是沉默的。
    """
    name = Config().api_key_env
    probe = _hand_written_key_name_sites
    assert probe(f'KEY_ENV = "{name}"\n', "a.py"), "绑给一个名字那一形没收到"
    assert probe(f'import os\nos.environ["{name}"] = "x"\n', "b.py"), "下标赋值那一形没收到"
    assert probe(f'import os\nos.environ.get("{name}")\n', "c.py"), "交给调用点那一形没收到"
    assert probe(f'env = {{"{name}": "x"}}\n', "d.py"), "写进字典那一形没收到"
    # 名字是断言的内容、或者由配置供给——两种都不是"第二个作者"
    assert probe(f'assert "{name}" in err\n', "e.py") == [], "把名字当断言内容收进来了"
    assert probe("from wolfengine.config import Config\n"
                 f'KEY_ENV = Config().api_key_env\n', "f.py") == [], "派生的那一形被误收"


# ---------------------------------- 每一处擦值调用点都要交出它擦的是哪个变量的名字

def _names_the_deployed_variable(arg: ast.expr) -> bool:
    """交给它的那个表达式，真的跟着部署走吗。

    两形"看着是名字、其实永远是出厂名"要拒：字符串字面量，和**从类上直接取**的那一枚
    （`Config.api_key_env` 读的是字段默认值，实例改名之后它不变——擦值两本的签名上曾各带一枚它的默认值，
    `#317`／`#318` 把它们摘了；`#319` 现量这一形在活代码里 0 枚，只剩判据的合成证人还写着它）。从实例上取的都放行（`self.cfg.api_key_env`、`CONF.api_key_env`、`Config()` 打头
    的那一枚），以及局部名——静态读不出它绑的是谁，但它的失效方向是"当场报错"，不是"静默退回出厂名"。
    """
    if isinstance(arg, ast.Constant):
        return False
    return not (isinstance(arg, ast.Attribute) and arg.attr == "api_key_env"
                and isinstance(arg.value, ast.Name) and arg.value.id == "Config")


def _scrub_call_sites(src: str, rel: str, target: str | set[str] = "_scrub_string", at: int = 1) -> list[tuple[int, str, bool, str]]:
    """每一处 `_scrub_string(...)` 的调用点：行号、归属函数、有没有交出**跟着部署走**的名字、出处。

    `target` 是被调用者的名字：默认正本里那一枚 `_scrub_string`，加宽的那一支把进口别名那一组交给它
    （由 `_scrub_alias_names` 现算），`at` 是名字在实参里的下标。归属按最近的 enclosing function 算，模块
    顶层那一档记成 `<module>`——不然"写在函数体外面的一处漏擦"会整类躲掉：判据看不见的那一类既不红也不进分母（`#226`/`#242` 那一族）。挂在对象上的那一形（`t._scrub_string(...)`）也算调用点。
    """
    tree = ast.parse(src)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def owner_of(node: ast.AST) -> str:
        while (node := parent.get(node)) is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    names, out = ({target} if isinstance(target, str) else set(target)), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        called = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
        if called not in names:
            continue
        given = node.args[at] if len(node.args) > at else next(
            (k.value for k in node.keywords if k.arg == "key_env"), None)
        named = given is not None and _names_the_deployed_variable(given)
        out.append((node.lineno, owner_of(node), named, rel))
    return out


def test_every_scrub_call_site_in_the_production_face_names_the_variable():
    """擦值调用点一枚都不许无名——上一片留给 `redact()` 的那格豁免，这一片收回了。

    `transport.py` 的 `_scrub_string()` 签名过去带一枚出厂名默认值（`#317` 那一片摘的），那句 docstring 是
    **所有调用点**的主张，而它此前零读者：改名部署下少传一枚名字从 `#317` 起当场 `TypeError`，交错一枚（字面量或从类上取的那一枚）才静默——那一路擦的还是出厂名，整套离线
    一条不红。现量的两向：`#314` 的两具同形刀里，落在非 200 正文那一支的能红（有一条用例点名改过
    名字的键），落在"200 而 body 不可解析"那一支的整套 1272 passed 全绿——那一格写不出行为证人
    （它擦的那串由 `JSONDecodeError` 拼出来，而这条异常的话里只有位置没有正文），所以这一格只能由
    形状判据认领。豁免按"归属哪个函数"划，宽谓词会把将来任何一处漏擦都放过去；名字接进落盘那一步
    之后 `redact()` 自己也把名字交了，那一格就没有结构上的理由留下，判据收成"一枚都不许无名"。
    第二参数光"有"还不够：第一版判据只数参数个数，K174 那具刀（把名字换成从类上直接取的那一枚）在它
    面前是绿的，实测 45 passed——"交了个名字"和"交的是部署改名之后的那个名字"不是一件事。
    """
    sites: list[tuple[int, str, bool, str]] = []
    for face in ("src", "scripts", "tests"):
        for book in sorted((ROOT / face).rglob("*.py")):
            if "__pycache__" in book.parts:
                continue
            text = book.read_text(encoding="utf-8")
            if "_scrub_string" not in text:
                continue
            sites += _scrub_call_sites(text, str(book.relative_to(ROOT)))
    loose = [s for s in sites if not s[2]]
    assert not loose, f"这些擦值调用点没交出跟着部署走的名字，改名部署下会退回出厂名：{loose}"
    named = [s for s in sites if s[2]]
    assert len(named) >= 5, \
        f"只扫到 {len(named)} 枚带名字的调用点：语料或谓词坏了，这条在发空集合格证"
    assert {s[3] for s in sites} == {"src/wolfengine/transport.py"}, \
        f"擦值只住在正本那一本里，别处再长出一枚要先说清它替谁擦：{sites}"


def test_the_scrub_site_probe_fires_on_each_shape_and_only_on_the_exempt_owner():
    """十形各一具合成证人，再加两形负控制。

    正控制不靠仓库里恰好有几枚调用点：仓库会被修干净，而尺比形状窄那种错在修干净之后是沉默的。
    """
    probe = _scrub_call_sites
    assert probe("def chat():\n    return _scrub_string(text)\n", "a.py") == \
        [(2, "chat", False, "a.py")], "漏传那一形没收到"
    assert probe("def chat():\n    return _scrub_string(text, env)\n", "b.py")[0][2], "位置形没认成带名字"
    assert probe("def chat():\n    return _scrub_string(text, key_env=env)\n", "c.py")[0][2], \
        "关键字形没认成带名字"
    assert probe("import t\n\ndef chat():\n    return t._scrub_string(text)\n", "d.py") == \
        [(4, "chat", False, "d.py")], "挂在对象上的那一形没收到"
    assert probe("def redact(o):\n    return _scrub_string(o)\n", "e.py")[0][1] == "redact", \
        "豁免按归属函数认，那一形没落进 redact 名下"
    assert probe("class C:\n    def m(self):\n        return _scrub_string(text)\n", "i.py") == \
        [(3, "m", False, "i.py")], "方法体里的那一形没收到"
    assert probe("_scrub_string(o)\n", "f.py") == [(1, "<module>", False, "f.py")], "顶层那一形没收到"
    # 交了名字、但那名字永远等于出厂名的那两形，和"没交"是同一件事
    assert probe("def chat():\n    return _scrub_string(text, Config.api_key_env)\n", "j.py") == \
        [(2, "chat", False, "j.py")], "从类上直接取的那一枚被认成了跟着部署走的名字"
    assert probe("def chat():\n    return _scrub_string(text, 'SOME_FIXED_NAME')\n", "k.py") == \
        [(2, "chat", False, "k.py")], "字面量那一枚被认成了名字"
    assert probe("def chat(self):\n    return _scrub_string(text, self.cfg.api_key_env)\n", "l.py")[0][2], \
        "生产里那一形（从实例上取）被误拒"
    # 负控制：散文里出现这个形状不是调用点
    assert probe('"""这一形写在 docstring 里。"""\n', "g.py") == []
    assert probe("# 这一形写在注释里。\n", "h.py") == []


def _scrub_alias_names(src: str) -> set[str]:
    """这一本里绑到正本 `transport.redact` 的那几个名字，别名也算。

    只认 `from …transport import redact [as 名]` 那一形：本地同名的函数、从别处进口的同名函数都不进
    语料，否则判据会把"另一本里恰好同名的东西"当成擦值点。`from wolfengine import transport` 再打
    `transport.redact(…)` 的那一形不在这一族里——现测全仓库 0 枚，真要长出来由 `_scrub_call_sites`
    的属性分支收，只是别名集合得先认识那种写法。
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("transport"):
            for alias in node.names:
                if alias.name == "redact":
                    names.add(alias.asname or "redact")
    return names


def _borrowed_sites(src: str, rel: str = "a.py") -> list[tuple[int, str, bool, str]]:
    """按别名集合调用同一枚尺子——判据只住一处，正控制才有意义（`#153` 那一族）。"""
    names = _scrub_alias_names(src)
    return _scrub_call_sites(src, rel, names) if names else []


def test_the_alias_probe_fires_on_the_borrowing_shapes_and_only_on_them():
    """七形各一具合成证人：正控制不靠仓库里恰好有几枚借用点。

    仓库会被修干净，而尺比形状窄那种错在修干净之后是沉默的。最后两形是"不该进语料"的负控制：
    本地同名函数与别处的同名进口，收进来就是把判据的射程从"借正本擦值"改成了"凡叫 redact 都管"。
    """
    assert _borrowed_sites("from t.transport import redact\n\n\ndef f(o):\n    return redact(o)\n") == \
        [(5, "f", False, "a.py")], "借用正本的无名形没收到"
    assert _borrowed_sites("from t.transport import redact\n\n\ndef f(o, env):\n    return redact(o, env)\n")[0][2], \
        "位置形没认成带名字"
    assert _borrowed_sites(
        "from t.transport import redact\n\n\ndef f(o, env):\n    return redact(o, key_env=env)\n"
    )[0][2], "关键字形没认成带名字"
    assert _borrowed_sites(
        "from t.transport import redact as scrub\n\n\ndef f(o, env):\n    return scrub(o, env)\n"
    )[0][2], "改过名的那枚别名没进语料"
    assert _borrowed_sites(
        "from t.transport import redact\n\n\ndef f(o):\n    return redact(o, Config.api_key_env)\n"
    )[0][2] is False, "从类上直接取的那一枚被当成了名字——出厂名会盖掉部署名"
    assert _borrowed_sites("def f(o):\n    return redact(o)\n") == [], "本地同名函数被当成借用正本"
    assert _borrowed_sites("from elsewhere import redact\n\n\ndef f(o):\n    return redact(o)\n") == [], \
        "别处进口的同名函数被当成借用正本"


def test_every_borrowed_scrubber_call_site_in_the_production_face_names_the_variable():
    """把交名判据的语料面从 `_scrub_string` 加宽到"借正本 `redact()` 的那几本"。

    `#315` 那条判据按字面名收，正本自己的 `redact()` 一枚都不在它的语料里——现量的账（20:24:48Z 的
    AST 普查，`census316/`）：全仓库把正本进口再调用的只有四本，无名的一共四枚。`scripts/calibrate.py:68`
    那枚是转手，本片把 `KEY_ENV` 补上；其余三枚住在 `tests/` 里，它们的无名正是被测试的形状——
    `test_transport.py:463` 那一枚 `unnamed` 从 `#318` 起也交了名——擦值层不带默认值，不交名当场炸。
    所以加宽按**面**切、不按归属函数切：按函数豁免是 `#314` 刚收回的那种，将来任何一处新漏都会躲进去；
    生产面（`src/` 与 `scripts/`）一枚不许无名，将来任何一本新进口正本的自动进分母。测试面整面不扫，
    因为这条主张是关于"脚本发请求读哪一枚变量、擦值就得擦同一枚"，而测试不往磁盘写端点的回声。
    地板按下界钉（`#313` 的账：地板要按面各钉），面级名册另钉一本都不许多出来。
    """
    sites: list[tuple[int, str, bool, str]] = []
    for face in ("src", "scripts"):
        for book in sorted((ROOT / face).rglob("*.py")):
            if "__pycache__" in book.parts:
                continue
            sites += _borrowed_sites(book.read_text(encoding="utf-8"), str(book.relative_to(ROOT)))
    loose = [s for s in sites if not s[2]]
    assert not loose, f"这些借用正本的调用点没交出跟着部署走的名字，改名部署下会退回出厂名：{loose}"
    assert len(sites) >= 4, f"只扫到 {len(sites)} 枚借用点：语料或谓词坏了，这条在发空集合格证"
    assert {s[3] for s in sites} == {"src/wolfengine/llm.py", "scripts/calibrate.py"}, \
        f"借用擦值正本的那几本变了，先说清新来的那一本替谁擦：{sorted({s[3] for s in sites})}"


def test_the_scrub_helper_takes_no_factory_default_for_the_name_it_erases_by():
    """`_scrub_string` 的 `key_env` 必填：少交一枚名字要炸，不许静默退回出厂名。

    现量的账（21:07:48Z 的 AST 普查，80 本）：这枚 helper 的真调用点只有正本自己那一本的五枚，每一枚都把
    名字显式交进去，所以签名上那枚出厂名默认值今天**没有任何调用方走到**——它是 `#86` 那一族在"默认值"
    这一档的对应物。摘掉之后少交名字当场 `TypeError`，而不是悄悄擦错一枚变量（`#315` 给 `as_response_dict`
    立的正是同一形）。
    这一条只钉 helper 那一个签名：`redact()` 那一格 `#317` 当时登记成"留着"的边界（它有三枚测试调用方走默认
    值，其中一枚是故意的负控制），`#318` 把这三枚各交了名、把边界一并摘了，那一格从此由隔壁那条用例钉。
    `#287` K4 那一族付过的过收价（判据宽一分，红在两处）就是当时不并格的理由。
    """
    import inspect

    from wolfengine import transport
    param = inspect.signature(transport._scrub_string).parameters["key_env"]
    assert param.default is inspect.Parameter.empty, \
        f"`_scrub_string` 的 key_env 又带上了默认值 {param.default!r}：少交名字的那一路会静默退回出厂名"


def test_the_public_scrubber_takes_no_factory_default_for_the_name_it_erases_by():
    """`redact()` 的 `key_env` 也必填：`#317` 那条"留着"的边界从这一片起收了。

    现量的账（21:32:27Z 按绑定解的 AST 普查，80 本）：正本的真调用点八枚。生产面四枚本来就交名，
    `scripts/calibrate.py:68` 那一枚交的是 `KEY_ENV`；`tests/` 四枚里三枚走签名上那枚默认值，走到的
    都是同一个类属性。失效方向和 helper 那格同形而更大：`require_key()` 读的是实例上的名字，默认值却
    永远是出厂名，改名部署下明文只擦掉一半，而且没有一处调用方会因为忘了交名而变红。摘掉之后这三枚
    各交一次名，那枚故意的负控制改成交出厂名——它主张的"力气来自那个实参"从此只由实参之差决定，
    不再混入元数之差。边界跟着挪一格：`redact()` 递归两支交的是形参而不是默认值，这一条不管它们；
    测试面整面不扫那条（`#316`）也不动，但它的理由今天读出来是两半：测试不往磁盘写端点的回声，
    加上测试里那枚故意的负控制交的本就是字面量名——`_names_the_deployed_variable` 禁的那一形。
    """
    import inspect

    from wolfengine import transport
    param = inspect.signature(transport.redact).parameters["key_env"]
    assert param.default is inspect.Parameter.empty, \
        f"`redact()` 的 key_env 又带上了默认值 {param.default!r}：改名部署下这一路会静默只擦出厂名"


# ------------------------------ 广告面加宽到散文：教读者 export 的名字也要有读者

AD_EXPORT = re.compile(r"export\s+([A-Z][A-Z0-9_]{3,})")
PROSE_AD_FLOORS = {"手册": 2, "py 散文": 1}

# 这两串只有代码行里有形状：本文件的 docstring 与注释都在这条尺的语料面上，把原形贴进散文就会
# 让它成为自己收到的第 4 枚（`#140` 那格自噬的形状）。
_AD_POSITIVE = "照手册敲 `export WOLF_LLM_NOT_READ=1` 就能跑"
_AD_NEGATIVE = "`WOLF_LLM_NOT_READ` 这一枚没有读者，别照抄"


def _doc_and_comment_lines(text: str) -> list[tuple[int, str]]:
    """一本 `.py` 里的散文行：模块/类/函数 docstring 占的那些行，加整行注释。

    代码行不算广告面。取值的那一句是程序在问，不是文档在教；读者会照抄的是用法块与注释里的命令。
    """
    doc: set[int] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            head = body[0] if body else None
            if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant) \
                    and isinstance(head.value.value, str):
                doc.update(range(head.lineno, head.end_lineno + 1))
    return [(i, line) for i, line in enumerate(text.splitlines(), 1)
            if i in doc or line.lstrip().startswith("#")]


def _advertised_exports(text: str) -> list[str]:
    """一段文本里"教读者去 export 某个名字"的那一形收到的名字。"""
    return AD_EXPORT.findall(text)


def _prose_advertisements() -> list[tuple[str, str, int, str]]:
    """散文面上的每一条广告，格式是〈面, 出处, 行号, 名〉。

    两面：手册五本逐行——那份名册住在 `test_doc_citations.py` 的 `_manual_pages()` 里，不抄第二份；
    加三棵 `.py` 的散文行。归档不在面上，理由与它藏着的住户由下面那条控制用例点名。
    """
    from test_doc_citations import _manual_pages
    out: list[tuple[str, str, int, str]] = []
    for page in _manual_pages():
        for num, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            out += [("手册", page.name, num, name) for name in _advertised_exports(line)]
    for face in ("src", "scripts", "tests"):
        for book in sorted((ROOT / face).rglob("*.py")):
            rel = str(book.relative_to(ROOT))
            for num, line in _doc_and_comment_lines(book.read_text(encoding="utf-8")):
                out += [("py 散文", rel, num, name) for name in _advertised_exports(line)]
    return out


def test_no_prose_page_advertises_a_variable_the_code_never_reads():
    """`#146` 只管住 `.env.example` 那一页广告；手册与 `.py` 散文里也有一样的句子在教 export。

    它们共用同一把尺 `_env_names_the_code_reads()`，所以这一片只加面，不造第二枚判据（`#153`）。

    票面是 `#319` 留下的那一格：出厂密钥名在代码里只有一个作者（`#313`），可"散文里教你敲的那一句"
    从来没人核——`#313` 的探针在自己的 docstring 里就写着散文面不在射程。改名之后 README 的配置一节
    与体检脚本的用法块会留下一个没有读者的名字，而静默无效这一族的第一次事故正是这一形。

    这条写完即绿：定价那趟（22:51:39Z，账住在项目记忆 `census320/`）数到手册面 2 枚、`.py` 散文面
    1 枚，三条都指着真有读者的名字。它的牙由 `#320` 的变异电池还账，其中必须红的那一具是把
    `Config.api_key_env` 的出厂名改掉。

    两处"不收"各自挡着一类过收，所以都记在这里：
      - 归档整本不在面上。那一本里"报告假话的句子"和普查表的一列都凑得出广告形，把它们当广告会咬到
        历史复述；而"归档只多不少"（`#162`）决定这一族不能靠改归档里的句子变绿。下面那条控制用例
        钉着这个豁免有住户。
      - 只认 `export` 紧跟一个全大写名那一形，句子里光提到名字不算——判据比形状宽就会把"这个名字
        没有读者"这类正确的叙述收进来。
    """
    hits = _prose_advertisements()
    for face, floor in PROSE_AD_FLOORS.items():
        got = sum(1 for hit in hits if hit[0] == face)
        assert got >= floor, (
            f"{face} 面只扫到 {got} 枚广告（地板 {floor}）：这条会在塌掉的语料上发合格证")
    readers = _env_names_the_code_reads()
    unread = sorted({(where, num, name) for _, where, num, name in hits if name not in readers})
    assert not unread, f"这些散文在教读者 export 一个代码里没人取的变量：{unread}"


def test_the_prose_advertisement_probe_houses_the_archive_and_spares_a_mention():
    """三格：广告形收到、光提名字的放过、归档那一面的豁免里真有一户住在里面。

    正控制不靠仓库恰好有几枚广告——三面今天全都有读者，语料被修干净之后"扫不到东西"这种错是沉默的。
    最后一格钉的是豁免不是免检：归档面一旦空了，并进语料面就没有代价，该并。
    """
    from test_doc_citations import ARCHIVE
    assert _advertised_exports(_AD_POSITIVE) == ["WOLF_LLM_NOT_READ"], "广告那一形没收到：整把尺没有牙"
    assert _advertised_exports(_AD_NEGATIVE) == [], "句子里光提到名字被当成了广告：判据比形状宽"
    readers = _env_names_the_code_reads()
    hidden = [(num, name)
              for num, line in enumerate(
                  (ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8").splitlines(), 1)
              for name in _advertised_exports(line) if name not in readers]
    assert hidden, "归档豁免这一格没有住户：它谁都不挡，该把归档并进语料面"
