"""
Boss Recruit - 打招呼前简历筛选
核心功能：卡片硬门槛 + 侧栏经历概览规则匹配（无 LLM），匹配才打招呼。

使用 persistent_context + 固定 seed 实现扫码一次永久免登。
"""

import json
import os
import re
import sys
import time
import random
from pathlib import Path
from datetime import datetime
from typing import Optional
from camoufox import Camoufox
from camoufox import launch_options

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本地缓存路径（提前定义，供 .env 读取使用）
SCRIPT_DIR = Path(__file__).parent.parent
SEEN_CANDIDATES_FILE = SCRIPT_DIR / "seen_candidates.json"
AUDIT_LOG_FILE = SCRIPT_DIR / "llm_audit_log.jsonl"
REPORTS_DIR = SCRIPT_DIR / "reports"
GREET_RUN_INDEX_FILE = REPORTS_DIR / "greet_run_index.json"

# 本轮最多「成功发出」的打招呼次数：会一直扫列表直到凑满或没有更多卡片（可用 BOSS_GREET_TOP 或 --top 覆盖）
DEFAULT_GREET_TOP = int(os.environ.get("BOSS_GREET_TOP", "20"))
# 滑到列表底部后卡片数连续若干次不增加则判定「暂无更多推荐」（避免死循环）
GREET_LIST_SCROLL_STALL_MAX = int(os.environ.get("BOSS_GREET_LIST_SCROLL_STALL", "5"))

# 本轮运行生成的判定报告路径（None 表示关闭报告）
_RULE_REPORT_PATH: Optional[Path] = None
_RULE_REPORT_RUN_SEQ: int = 0
_RULE_REPORT_DATE_LINE: str = ""


def _allocate_greet_run_sequence() -> int:
    """递增并持久化「第几次打招呼任务」，便于跨多次运行核查。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    last = 0
    if GREET_RUN_INDEX_FILE.exists():
        try:
            data = json.loads(GREET_RUN_INDEX_FILE.read_text(encoding="utf-8"))
            last = int(data.get("last_seq", 0))
        except Exception:
            last = 0
    seq = last + 1
    try:
        GREET_RUN_INDEX_FILE.write_text(
            json.dumps(
                {
                    "last_seq": seq,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return seq


def init_rule_report_session(top: int, argv_summary: str = "") -> None:
    """每次运行创建一个带时间戳的 txt，便于回看判定结果。"""
    global _RULE_REPORT_PATH, _RULE_REPORT_RUN_SEQ, _RULE_REPORT_DATE_LINE
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    _RULE_REPORT_RUN_SEQ = _allocate_greet_run_sequence()
    date_cn = now.strftime("%Y年%m月%d日")
    date_iso = now.strftime("%Y-%m-%d")
    _RULE_REPORT_DATE_LINE = f"{date_cn}（{date_iso}）"
    ts = now.strftime("%Y%m%d_%H%M%S")
    _RULE_REPORT_PATH = (
        REPORTS_DIR
        / f"greet_rule_report_run{_RULE_REPORT_RUN_SEQ:04d}_{ts}.txt"
    )
    with open(_RULE_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("Boss Recruit — 简历规则判定报告\n")
        f.write(f"报告日期: {_RULE_REPORT_DATE_LINE}\n")
        f.write(f"任务序号: 第 {_RULE_REPORT_RUN_SEQ} 次运行（技能目录累计，见 reports/greet_run_index.json）\n")
        f.write(f"生成时间: {now.isoformat(timespec='seconds')}\n")
        f.write(f"计划打招呼上限 top={top}\n")
        if argv_summary:
            f.write(f"命令行: {argv_summary}\n")
        f.write("=" * 72 + "\n\n")
    print(
        f"[report] 判定报告（第{_RULE_REPORT_RUN_SEQ}次 · {_RULE_REPORT_DATE_LINE}）: {_RULE_REPORT_PATH}"
    )


def disable_rule_report_session() -> None:
    global _RULE_REPORT_PATH, _RULE_REPORT_RUN_SEQ, _RULE_REPORT_DATE_LINE
    _RULE_REPORT_PATH = None
    _RULE_REPORT_RUN_SEQ = 0
    _RULE_REPORT_DATE_LINE = ""


def append_rule_report(
    candidate_name: str,
    card_text: str,
    summary_text: str,
    result: dict,
    *,
    tag: str = "",
) -> None:
    """追加一条判定记录到本轮报告文件。"""
    if _RULE_REPORT_PATH is None:
        return
    sep = "-" * 72
    lines = [
        sep,
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if tag:
        lines.append(f"类型: {tag}")
    lines.extend(
        [
            f"候选人: {candidate_name}",
            f"is_match: {result.get('is_match')}",
            f"score: {result.get('score')}",
            f"reason: {result.get('reason')}",
        ]
    )
    seo = result.get("matched_seo_keywords") or []
    if seo:
        lines.append(f"SEO词库命中: {', '.join(seo)}")
    kw = result.get("matched_combined_keywords") or []
    if kw:
        lines.append(f"综合词库命中({len(kw)}): {', '.join(kw)}")
    if result.get("gap_note"):
        lines.append(f"经历: {result['gap_note']}")
    if result.get("score_formula"):
        lines.append(f"分值: {result['score_formula']}")
    card_snip = (card_text or "").replace("\r", "").strip()
    if len(card_snip) > 600:
        card_snip = card_snip[:600] + "…"
    lines.append(f"卡片摘要:\n{card_snip}")
    sum_snip = (summary_text or "").replace("\r", "").strip()
    if len(sum_snip) > 1200:
        sum_snip = sum_snip[:1200] + "…"
    if sum_snip:
        lines.append(f"简历/侧栏摘要:\n{sum_snip}")
    lines.append("")
    try:
        with open(_RULE_REPORT_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"[report] WARN 写入报告失败: {e}")


def finalize_rule_report_session(greeted: list, stopped_reason: str = "") -> None:
    """运行结束时写入汇总。"""
    if _RULE_REPORT_PATH is None:
        return
    try:
        with open(_RULE_REPORT_PATH, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 72 + "\n")
            f.write(f"报告日期: {_RULE_REPORT_DATE_LINE}\n")
            f.write(f"任务序号: 第 {_RULE_REPORT_RUN_SEQ} 次运行\n")
            f.write(f"结束时间: {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"实际发出招呼人数: {len(greeted)}\n")
            if greeted:
                for g in greeted:
                    f.write(
                        f"  - {g.get('name','?')} | score={g.get('score')} | {g.get('reason','')}\n"
                    )
            if stopped_reason:
                f.write(f"备注: {stopped_reason}\n")
    except OSError as e:
        print(f"[report] WARN 写入汇总失败: {e}")

# MiniMax LLM 配置
# 优先从环境变量读取，其次从 .env 文件读取
OPENAI_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
if not OPENAI_API_KEY:
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("MINIMAX_API_KEY="):
                OPENAI_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
# OpenAI 兼容接口；模型 ID 须与控制台/文档一致（例如 MiniMax-M2.7，勿写成「MiniMax M2.7」带空格）
OPENAI_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
LLM_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7")

# 卡片求职状态：以下任一为通过（第一步筛选）
CARD_ALLOWED_JOB_STATUS = re.compile(
    r"(离职\s*[-－]\s*随时到岗|在职\s*[-－]\s*考虑机会|在职\s*[-－]\s*月内到岗)"
)

# 抓取到的在线简历至少多长才调用 LLM（过短多为仅有时间线）
MIN_RESUME_TEXT_LEN_FOR_LLM = int(os.environ.get("MIN_RESUME_TEXT_LEN_FOR_LLM", "200"))

# 规则筛选：最近一段工作经历结束不得早于该年月（含）
RULE_MIN_LAST_JOB_END = (2025, 11)
# 规则筛选：距最近一段结束超过该月数则否决（与上方同时满足）
RULE_MAX_GAP_MONTHS = 6

# 侧栏「经历概览」里职位/文本需命中 SEO 相关（小写匹配）
RULE_SIDEBAR_SEO_SUBSTR = tuple(
    x.strip().lower()
    for x in os.environ.get(
        "RULE_SIDEBAR_SEO_SUBSTR",
        "seo,技术seo,seo运营,搜索引擎优化,海外seo,seo文案,seo策划,seo推广,谷歌seo,google seo,站内优化",
    ).split(",")
    if x.strip()
)

# 卡片+侧栏合并文本需至少命中其一（小写匹配 google）
RULE_COMBINED_ANY_SUBSTR = tuple(
    x.strip().lower()
    for x in os.environ.get(
        "RULE_COMBINED_ANY_SUBSTR",
        "seo,seo运营,b端,google,谷歌,独立站,跨境电商,跨境b端,semrush,ahrefs,search console,自然流量,关键词,外链,内链,技术seo,搜索引擎,geo",
    ).split(",")
    if x.strip()
)

# 点击卡片后监听 wapi JSON，尝试还原 Canvas 内不可读的简历正文（设为 0 可关闭）
CAPTURE_RESUME_WAPI = os.environ.get("BOSS_CAPTURE_RESUME_WAPI", "1").lower() not in (
    "0",
    "false",
    "no",
)


def card_has_allowed_job_status(card_text: str) -> bool:
    """离职-随时到岗 / 在职-考虑机会 / 在职-月内到岗 任一即 True。"""
    if not card_text:
        return False
    return bool(CARD_ALLOWED_JOB_STATUS.search(card_text))


def parse_age_from_card(card_text: str):
    """从卡片文案解析年龄，如「25岁」。解析不到返回 None。"""
    if not card_text:
        return None
    m = re.search(r"(\d{2})岁", card_text)
    if m:
        return int(m.group(1))
    return None


def parse_education_gate(card_text: str):
    """
    学历硬门槛：仅「本科及以上」进入后续流程。
    返回 (True,) 表示达标；(False, reason) 表示简历学历低于本科；
    (None, '') 表示卡片未识别到学历关键词，不拦截（交给在线简历+LLM）。
    """
    t = card_text or ""
    if re.search(r"博士后|博士研究生|(?<!后)博士", t):
        return True, ""
    if re.search(r"硕士研究生|硕士|研究生|MBA|EMBA", t):
        return True, ""
    if re.search(r"统招本科|自考本科|成人本科|专升本|本科|学士", t):
        return True, ""
    if re.search(r"大专|专科|高职|高中|中专|技校|职高", t):
        return False, "学历低于本科，跳过"
    return None, ""


def card_header_gate(card_text: str, max_age: int):
    """
    卡片表头即可判定的规则（不打招呼、不打开侧栏）。
    返回 (reject, reason)。reject=True 表示跳过此人。
    """
    age = parse_age_from_card(card_text)
    if age is not None and age > max_age:
        return True, f"年龄{age}岁大于{max_age}岁，跳过"
    edu_ok, edu_reason = parse_education_gate(card_text)
    if edu_ok is False:
        return True, edu_reason
    if edu_ok is None:
        return True, "卡片未标明本科及以上学历，跳过"
    if not card_has_allowed_job_status(card_text):
        return (
            True,
            "求职状态不在允许范围（需：离职-随时到岗 / 在职-考虑机会 / 在职-月内到岗），跳过",
        )
    return False, ""


def _months_since_job_end(end_y: int, end_m: int, now: datetime) -> int:
    return (now.year - end_y) * 12 + (now.month - end_m)


def parse_sidebar_latest_job_gap(sidebar_text: str):
    """
    从侧栏经历概览（或 wapi 拼入的时间线）取「第一段」工作时间：YYYY.MM - YYYY.MM / 至今。
    返回 (gap_months, detail_str) ；无法解析返回 (None, reason)。
    gap_months==0 表示在职/至今。
    """
    s = sidebar_text or ""
    if not re.search(r"\d{4}\.\d{2}", s):
        return None, "侧栏无经历时间线"

    m_now = re.search(r"\d{4}\.\d{2}\s*[-－]\s*(至今|现在|在职)", s)
    if m_now:
        return 0, "最近一段仍为在职/至今"

    m = re.search(r"\d{4}\.\d{2}\s*[-－]\s*(\d{4})\.(\d{2})", s)
    if not m:
        return None, "未解析到 YYYY.MM - YYYY.MM"

    ey, em = int(m.group(1)), int(m.group(2))
    now = datetime.now()
    gap = _months_since_job_end(ey, em, now)
    return gap, f"最近一段结束 {ey}.{em:02d}，距今约{gap}个月"


def rule_based_match(card_text: str, sidebar_text: str) -> dict:
    """
    基于卡片 + 侧栏文本的规则匹配（无 LLM）。
    sidebar_text 可含从 wapi 拼入的时间线/职位片段。
    返回 {"is_match": bool, "score": int, "reason": str}
    """
    combined = ((card_text or "") + "\n" + (sidebar_text or "")).lower()
    sb = combined

    gap, gap_note = parse_sidebar_latest_job_gap(sidebar_text or "")
    if gap is None:
        return {"is_match": False, "score": 0, "reason": gap_note}

    if gap > RULE_MAX_GAP_MONTHS:
        return {
            "is_match": False,
            "score": 0,
            "reason": f"空窗>{RULE_MAX_GAP_MONTHS}个月（{gap_note}）",
        }

    m_end = re.search(r"\d{4}\.\d{2}\s*[-－]\s*(\d{4})\.(\d{2})", sidebar_text or "")
    if m_end and gap > 0:
        ey, em = int(m_end.group(1)), int(m_end.group(2))
        if (ey, em) < RULE_MIN_LAST_JOB_END:
            return {
                "is_match": False,
                "score": 0,
                "reason": f"最近工作结束早于{RULE_MIN_LAST_JOB_END[0]}.{RULE_MIN_LAST_JOB_END[1]:02d}（{ey}.{em:02d}）",
            }

    matched_seo = [k for k in RULE_SIDEBAR_SEO_SUBSTR if k in sb]
    if not matched_seo:
        return {
            "is_match": False,
            "score": 0,
            "reason": "卡片与经历文本中未出现SEO相关岗位/关键词",
        }

    matched_kw = [k for k in RULE_COMBINED_ANY_SUBSTR if k in combined]
    if not matched_kw:
        return {
            "is_match": False,
            "score": 0,
            "reason": "卡片+侧栏未命中SEO运营/B端/Google等关键词",
        }

    hits = len(matched_kw)
    raw_score = 70 + hits * 5
    score = min(100, raw_score)
    return {
        "is_match": True,
        "score": score,
        "reason": f"规则通过（{gap_note}；综合命中约{hits}项）",
        "matched_seo_keywords": matched_seo,
        "matched_combined_keywords": matched_kw,
        "gap_months": gap,
        "gap_note": gap_note,
        "score_formula": f"min(100, 70 + {hits}×5) = min(100, {raw_score}) = {score}",
    }


def resume_text_sufficient_for_llm(text: str):
    """
    已尽力抓取在线简历全文后再调 LLM：长度门槛 + 弱启发「是否有工作内容类描述」。
    返回 (True, '') 或 (False, reason)。
    """
    s = (text or "").strip()
    if len(s) < MIN_RESUME_TEXT_LEN_FOR_LLM:
        return False, f"在线简历过短({len(s)}字<{MIN_RESUME_TEXT_LEN_FOR_LLM})，跳过LLM"
    work_pat = re.compile(
        r"(工作职责|工作内容|职位描述|主要负责|工作描述|项目描述|职责[:：]|业绩|工作业绩|询盘|转化率|SEO|优化)"
    )
    if work_pat.search(s):
        return True, ""
    if len(s) >= max(MIN_RESUME_TEXT_LEN_FOR_LLM * 2, 480):
        return True, ""
    return False, "在线简历中未识别到工作内容/成果类描述，跳过LLM"


# 右侧简历抽屉延迟加载：轮询超时（秒）
RESUME_PANEL_POLL_TIMEOUT_SEC = float(os.environ.get("RESUME_PANEL_POLL_TIMEOUT_SEC", "28"))

# 点击卡片后先短暂停顿，再给侧栏请求留出时间
POST_CARD_CLICK_PAUSE_SEC = float(os.environ.get("POST_CARD_CLICK_PAUSE_SEC", "0.8"))

# 规则匹配通过后等待多久再点「打招呼」（秒）
GREET_AFTER_MATCH_WAIT_SEC = float(os.environ.get("GREET_AFTER_MATCH_WAIT_SEC", "30"))

# 推荐牛人列表顶部「筛选」面板（环境变量可覆盖，逗号分隔）
BOSS_FILTER_DEGREE_TAGS = [
    x.strip()
    for x in os.environ.get("BOSS_FILTER_DEGREE_TAGS", "本科").split(",")
    if x.strip()
]
BOSS_FILTER_JOB_TAGS = [
    x.strip()
    for x in os.environ.get(
        "BOSS_FILTER_JOB_TAGS",
        "离职-随时到岗,在职-考虑机会,在职-月内到岗",
    ).split(",")
    if x.strip()
]

# 弹层里区块标题（与页面 div.name 文案一致）
BOSS_FILTER_SECTION_DEGREE = os.environ.get("BOSS_FILTER_SECTION_DEGREE", "学历要求")
BOSS_FILTER_SECTION_JOB = os.environ.get("BOSS_FILTER_SECTION_JOB", "求职意向")

# 筛选面板节奏（秒）：偏稳可用更大值；偏快若偶发漏选可提高 *_PAUSE_SEC）
BOSS_FILTER_START_PAUSE_SEC = float(os.environ.get("BOSS_FILTER_START_PAUSE_SEC", "0.55"))
BOSS_FILTER_AFTER_TRIGGER_PAUSE_SEC = float(
    os.environ.get("BOSS_FILTER_AFTER_TRIGGER_PAUSE_SEC", "0.5")
)
BOSS_FILTER_AFTER_OPTION_PAUSE_SEC = float(
    os.environ.get("BOSS_FILTER_AFTER_OPTION_PAUSE_SEC", "0.14")
)
BOSS_FILTER_AFTER_CONFIRM_PAUSE_SEC = float(
    os.environ.get("BOSS_FILTER_AFTER_CONFIRM_PAUSE_SEC", "1.15")
)
BOSS_FILTER_VIP_DISMISS_PAUSE_SEC = float(
    os.environ.get("BOSS_FILTER_VIP_DISMISS_PAUSE_SEC", "0.22")
)


def _click_filter_trigger(page, frame):
    """
    右上角「筛选」。推荐使用牛人列表头漏斗控件：
    .candidate-head → .filter-ui-recommend-filter → .filter-label-wrap（图标，可无「筛选」二字）。
    """
    boss_filter_dom_selectors = [
        ".candidate-head .filter-label-wrap",
        ".filter-ui-recommend-filter.op-filter .filter-label-wrap",
        ".filter-ui-recommend-filter .filter-label-wrap",
        ".ssr-operate .filter-label-wrap",
        ".filter-label-wrap",
        ".candidate-head .filter-label",
        ".filter-ui-recommend-filter .filter-label",
    ]
    for sel in boss_filter_dom_selectors:
        try:
            loc = frame.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点击筛选 ({sel})")
                return True
        except Exception:
            continue
        try:
            loc = page.locator(f'iframe[name="recommendFrame"] >> {sel}').first
            if loc.is_visible(timeout=1200):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点击筛选 (page>>iframe>>{sel})")
                return True
        except Exception:
            continue

    js_click_filter_icon = r"""() => {
        const pick =
            document.querySelector('.candidate-head .filter-label-wrap') ||
            document.querySelector('.filter-ui-recommend-filter.op-filter .filter-label-wrap') ||
            document.querySelector('.filter-ui-recommend-filter .filter-label-wrap') ||
            document.querySelector('.ssr-operate .filter-label-wrap') ||
            document.querySelector('.filter-label-wrap');
        if (!pick || !pick.getBoundingClientRect) return '';
        const r = pick.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return '';
        pick.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        pick.click();
        return pick.className || 'filter-label-wrap';
    }"""

    try:
        icon_hit = frame.locator("body").first.evaluate(js_click_filter_icon)
        if icon_hit:
            print(f"[filter-ui] 已点击筛选 (icon-dom: {icon_hit[:80]})")
            return True
    except Exception:
        pass
    try:
        for fr in getattr(page, "frames", None) or []:
            if getattr(fr, "name", None) != "recommendFrame":
                continue
            icon_hit = fr.evaluate(js_click_filter_icon)
            if icon_hit:
                print(f"[filter-ui] 已点击筛选 (icon-dom frame: {icon_hit[:80]})")
                return True
    except Exception:
        pass

    js_smart_filter_click = r"""() => {
        const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const badAncestor = (el) => {
            let p = el;
            for (let i = 0; i < 14 && p; i++, p = p.parentElement) {
                const cls = (p.className && String(p.className)) || '';
                if (/card-inner|card-list|card-item|resume-list|geek-card|job-card/i.test(cls))
                    return true;
                if (/candidate-card/i.test(cls)) return true;
                if (/^candidate$/i.test(cls) && !/candidate-head/i.test(cls)) return true;
                if (p.getAttribute && (p.getAttribute('data-geekid') || p.getAttribute('data-geek')))
                    return true;
            }
            return false;
        };
        const labelOk = (t) => {
            if (!t) return false;
            if (t === '筛选') return true;
            return /^筛选\s*[\u25bc\u25bd\u2228▼▽]?$/.test(t) && t.length <= 8;
        };
        const hits = [];
        const sel =
            'button, [role="button"], a, span, div, label';
        document.querySelectorAll(sel).forEach((el) => {
            if (!el.getBoundingClientRect || badAncestor(el)) return;
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return;
            if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) return;
            const t = norm(el.innerText);
            if (!labelOk(t)) return;
            if (t.length > 10) return;
            const cx = r.left + r.width / 2;
            const cy = r.top + r.height / 2;
            const inToolbar = cy < vh * 0.52 && cx > vw * 0.38;
            hits.push({ el, cy, cx, inToolbar, area: r.width * r.height, t });
        });
        if (!hits.length) return '';
        hits.sort((a, b) => {
            if (a.inToolbar !== b.inToolbar) return a.inToolbar ? -1 : 1;
            if (Math.abs(a.cy - b.cy) > 3) return a.cy - b.cy;
            return b.cx - a.cx;
        });
        const pick = hits[0];
        pick.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        pick.el.click();
        return pick.t + '|toolbar=' + pick.inToolbar + '|y=' + Math.round(pick.cy);
    }"""

    def _eval_js_click():
        try:
            return frame.locator("body").first.evaluate(js_smart_filter_click)
        except Exception:
            pass
        try:
            for fr in getattr(page, "frames", None) or []:
                if getattr(fr, "name", None) != "recommendFrame":
                    continue
                hit = fr.evaluate(js_smart_filter_click)
                if hit:
                    return hit
        except Exception:
            pass
        return ""

    hit = _eval_js_click()
    if hit:
        print(f"[filter-ui] 已点击筛选 (smart-js: {hit})")
        return True

    narrow_selectors = [
        '[class*="filter"] button:has-text("筛选")',
        '[class*="toolbar"] button:has-text("筛选")',
        '[class*="header"] button:has-text("筛选")',
        '[class*="title-bar"] button:has-text("筛选")',
        '[class*="operation"] button:has-text("筛选")',
        'button:has-text("筛选")',
        '[role="button"]:has-text("筛选")',
        '[class*="filter-btn"]',
        '[class*="Filter"] button:has-text("筛选")',
    ]
    for sel in narrow_selectors:
        try:
            loc = frame.locator(sel).first
            if loc.is_visible(timeout=900):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点击筛选 ({sel})")
                return True
        except Exception:
            continue
        try:
            loc = page.locator(f'iframe[name="recommendFrame"] >> {sel}').first
            if loc.is_visible(timeout=900):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点击筛选 (page>>iframe>>{sel})")
                return True
        except Exception:
            continue

    try:
        loc = frame.get_by_role("button", name="筛选", exact=True).first
        if loc.is_visible(timeout=900):
            loc.click(timeout=6000)
            print("[filter-ui] 已点击筛选 (get_by_role button 筛选)")
            return True
    except Exception:
        pass

    return False


def _dismiss_filter_vip_layer(page):
    """VIP 营销层可能挡住选项，尝试关掉。"""
    for txt in ("暂不需要", "我知道了", "关闭", "跳过"):
        try:
            loc = page.locator(f"text={txt}").first
            if loc.is_visible(timeout=400):
                loc.click(timeout=2000)
                time.sleep(BOSS_FILTER_VIP_DISMISS_PAUSE_SEC)
                print(f"[filter-ui] 已关闭弹层提示: {txt}")
        except Exception:
            continue


def _click_tag_in_dialog(page, label: str, exact: bool = True) -> bool:
    """兜底：全页按文案点击（易误点，优先用 _click_filter_modal_option）。"""
    if not label:
        return False
    tries = []
    if exact:
        tries.append(("get_by_text_exact", lambda: page.get_by_text(label, exact=True).first))
    tries.append(("locator_text", lambda: page.locator(f"text={label}").first))
    tries.append(("button", lambda: page.locator(f'button:has-text("{label}")').first))
    tries.append(("div.option", lambda: page.locator(f'div.option:has-text("{label}")').first))
    for name, get_loc in tries:
        try:
            loc = get_loc()
            if loc.is_visible(timeout=800):
                loc.click(timeout=4000)
                print(f"[filter-ui] 已选: {label} ({name})")
                time.sleep(BOSS_FILTER_AFTER_OPTION_PAUSE_SEC)
                return True
        except Exception:
            continue
    return False


def _click_filter_modal_option(ctx, section_title: str, option_text: str) -> bool:
    """
    Boss VIP 筛选弹层结构：div.filter-item → div.name（区块标题）→ div.options → div.option。
    在指定区块内点选项，避免点到其它行的同名文案。
    """
    if not option_text or not section_title:
        return False
    try:
        item = ctx.locator("div.filter-item").filter(
            has=ctx.locator("div.name", has_text=section_title)
        ).first
        item.wait_for(state="visible", timeout=6000)
        pat = re.compile("^" + re.escape(option_text.strip()) + "$")
        opt = item.locator("div.option").filter(has_text=pat).first
        if opt.is_visible(timeout=1500):
            opt.click(timeout=5000)
            print(f"[filter-ui] 已选 [{section_title}] → {option_text}")
            time.sleep(BOSS_FILTER_AFTER_OPTION_PAUSE_SEC)
            return True
    except Exception:
        pass
    try:
        item = ctx.locator("div.filter-item").filter(
            has=ctx.locator("div.name", has_text=section_title)
        ).first
        opt = item.get_by_text(option_text, exact=True).first
        if opt.is_visible(timeout=1200):
            opt.click(timeout=5000)
            print(f"[filter-ui] 已选 [{section_title}] → {option_text} (exact)")
            time.sleep(BOSS_FILTER_AFTER_OPTION_PAUSE_SEC)
            return True
    except Exception:
        pass
    return False


def _click_filter_modal_option_any_ctx(page, frame, section_title: str, option_text: str) -> bool:
    for ctx in (page, frame):
        if ctx is None:
            continue
        if _click_filter_modal_option(ctx, section_title, option_text):
            return True
    return False


def _click_confirm_filter(page, frame):
    """确定常为 div.btn；清除为 div.btn.btn-outline（见图）。"""
    js_confirm = r"""() => {
        const nodes = document.querySelectorAll('div.btn');
        for (const b of nodes) {
            if (!b.offsetParent) continue;
            const t = (b.innerText || '').trim();
            if (t !== '确定') continue;
            if (b.classList.contains('btn-outline')) continue;
            b.click();
            return true;
        }
        return false;
    }"""

    for ctx_name, ctx in (("page", page), ("frame", frame)):
        if ctx is None:
            continue
        try:
            loc = ctx.locator('div.btn:not(.btn-outline)').filter(has_text=re.compile(r"^\s*确定\s*$")).first
            if loc.is_visible(timeout=1000):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点确定 ({ctx_name}, div.btn)")
                return True
        except Exception:
            pass
        try:
            if ctx.evaluate(js_confirm):
                print(f"[filter-ui] 已点确定 ({ctx_name}, js)")
                return True
        except Exception:
            pass
        try:
            loc = ctx.locator('button:has-text("确定")').first
            if loc.is_visible(timeout=600):
                loc.click(timeout=6000)
                print(f"[filter-ui] 已点确定 ({ctx_name}, button)")
                return True
        except Exception:
            continue
    return False


def apply_recommend_talent_filters(page, frame):
    """
    进入推荐牛人且 iframe 就绪后：点右上角「筛选」→ 选学历/求职意向 → 确定 → 等待列表刷新。
    筛选项可通过环境变量 BOSS_FILTER_DEGREE_TAGS / BOSS_FILTER_JOB_TAGS 配置。
    """
    if frame is None:
        return False
    print("[filter-ui] 开始配置列表筛选…")
    time.sleep(BOSS_FILTER_START_PAUSE_SEC)
    if not _click_filter_trigger(page, frame):
        print("[filter-ui] WARN: 未能点击「筛选」，跳过筛选步骤（仍将处理当前列表）")
        return False
    time.sleep(BOSS_FILTER_AFTER_TRIGGER_PAUSE_SEC)
    _dismiss_filter_vip_layer(page)
    try:
        page.locator("text=学历要求").first.wait_for(state="visible", timeout=8000)
    except Exception:
        try:
            page.locator("text=求职意向").first.wait_for(state="visible", timeout=3000)
        except Exception:
            pass

    for tag in BOSS_FILTER_DEGREE_TAGS:
        if _click_filter_modal_option_any_ctx(page, frame, BOSS_FILTER_SECTION_DEGREE, tag):
            break
        if _click_tag_in_dialog(page, tag, exact=True):
            break
        if _click_tag_in_dialog(page, tag, exact=False):
            break

    chosen_job = 0
    for tag in BOSS_FILTER_JOB_TAGS:
        if _click_filter_modal_option_any_ctx(page, frame, BOSS_FILTER_SECTION_JOB, tag):
            chosen_job += 1
            continue
        if _click_tag_in_dialog(page, tag, exact=True):
            chosen_job += 1
        elif _click_tag_in_dialog(page, tag, exact=False):
            chosen_job += 1
    if chosen_job == 0:
        print("[filter-ui] WARN: 未点到任何求职意向选项，仍将尝试确定")
    else:
        print(f"[filter-ui] 求职意向已选 {chosen_job} 项")

    if not _click_confirm_filter(page, frame):
        print("[filter-ui] WARN: 未点到「确定」，筛选可能未生效")
        return False

    time.sleep(BOSS_FILTER_AFTER_CONFIRM_PAUSE_SEC)
    try:
        frame.locator(".card-inner").first.wait_for(state="visible", timeout=20000)
        print("[filter-ui] 列表已刷新，继续处理卡片")
    except Exception as e:
        print(f"[filter-ui] WARN: 等待卡片刷新: {e}")
    return True


def scroll_recommend_list(frame, page) -> None:
    """
    推荐牛人卡片在 iframe「recommendFrame」内，滚顶层 window 往往加载不到更多；
    改为滚 iframe 文档、最后一张卡片入屏，并轻微 wheel 兜底。
    """
    if frame is None:
        return
    try:
        frame.locator("body").first.evaluate(
            """() => {
                const root = document.documentElement || document.body;
                const h = Math.max(
                    root ? root.scrollHeight : 0,
                    document.body ? document.body.scrollHeight : 0,
                    0
                );
                window.scrollTo(0, h);
                if (root) root.scrollTop = root.scrollHeight;
                if (document.body) document.body.scrollTop = document.body.scrollHeight;
            }"""
        )
    except Exception:
        pass
    try:
        n = frame.locator(".card-inner").count()
        if n > 0:
            frame.locator(".card-inner").nth(n - 1).scroll_into_view_if_needed(
                timeout=8000
            )
    except Exception:
        pass
    try:
        page.mouse.wheel(0, 800)
    except Exception:
        pass
    time.sleep(1.45)


def expand_resume_sections(frame):
    """尽量点开右侧栏内的「展开/查看更多」，露出工作内容等折叠块。"""
    if frame is None:
        return
    clicked = 0
    # Boss 推荐牛人右侧「经历概览」：.resume-summary > ul.jobs > li，日期可能在折叠子节点内
    try:
        job_rows = frame.locator(".resume-summary ul.jobs:not(.education) > li")
        for i in range(min(job_rows.count(), 15)):
            try:
                row = job_rows.nth(i)
                if row.is_visible(timeout=400):
                    row.click(timeout=1500)
                    clicked += 1
                    time.sleep(0.15)
            except Exception:
                continue
    except Exception:
        pass
    for _ in range(15):
        try:
            btn = frame.locator("text=展开").first
            if not btn.is_visible(timeout=400):
                break
            btn.click(timeout=2500)
            clicked += 1
            time.sleep(0.35)
        except Exception:
            break
    for label in ("查看更多", "查看全部"):
        try:
            btn = frame.locator(f"text={label}").first
            if btn.is_visible(timeout=400):
                btn.click(timeout=2500)
                clicked += 1
                time.sleep(0.35)
        except Exception:
            pass
    if clicked:
        print(f"    [RESUME] 已尝试展开折叠区块 ({clicked} 次点击)")


def _greet_feedback_visible(frame, page) -> bool:
    """是否已出现招呼成功弹层（避免点到隐藏卡片上的假「成功」）。"""
    hints = (
        "已向牛人发送招呼",
        "知道了",
        "招呼发送成功",
        "发送成功",
        "今日打招呼",
    )
    for ctx in (frame, page):
        if ctx is None:
            continue
        for h in hints:
            try:
                if ctx.locator(f"text={h}").first.is_visible(timeout=400):
                    return True
            except Exception:
                continue
    return False


def _click_visible_greet_in_sidebar_js(frame):
    """
    只在当前展开的 .resume-right-side 内找可见的打招呼按钮，跳过「继续沟通」与隐藏副本。
    """
    js = r"""() => {
        const roots = [
            document.querySelector('.resume-right-side'),
            document.querySelector('.resume-simple-box'),
            document.querySelector('.dialog-footer'),
            document.body
        ].filter(Boolean);
        const candidates = [];
        const seen = new Set();
        for (const root of roots) {
            root.querySelectorAll('button.btn-greet').forEach(b => {
                if (!seen.has(b)) { seen.add(b); candidates.push(b); }
            });
        }
        const visibleOk = (b) => {
            if (!b || !b.offsetParent) return false;
            const t = (b.innerText || '').trim();
            if (t.includes('继续沟通')) return false;
            if (t.length > 0 && !t.includes('打招呼')) return false;
            const r = b.getBoundingClientRect();
            return r.width >= 20 && r.height >= 16 &&
                r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
        };
        for (const b of candidates) {
            if (!visibleOk(b)) continue;
            b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            b.click();
            return (b.innerText || '').trim() || 'btn-greet';
        }
        return '';
    }"""
    try:
        return frame.locator("body").first.evaluate(js)
    except Exception:
        return ""


def _click_scoped_greet_playwright(frame) -> str:
    """从侧栏相关容器由后往前找第一个可见的打招呼按钮（避开列表里靠前的隐藏副本）。"""
    scoped_selectors = [
        ".resume-right-side button.btn-greet",
        ".resume-simple-box button.btn-greet",
        ".communication .dialog-footer button.btn-greet",
        ".dialog-footer button.btn-greet",
    ]
    for sel in scoped_selectors:
        try:
            btns = frame.locator(sel)
            n = btns.count()
            for i in range(n - 1, max(-1, n - 15), -1):
                loc = btns.nth(i)
                if not loc.is_visible(timeout=800):
                    continue
                try:
                    txt = loc.inner_text(timeout=500) or ""
                except Exception:
                    txt = ""
                if "继续沟通" in txt:
                    continue
                if txt.strip() and "打招呼" not in txt:
                    continue
                loc.scroll_into_view_if_needed(timeout=5000)
                time.sleep(0.25)
                try:
                    loc.click(timeout=15000, force=True)
                except Exception:
                    loc.evaluate("el => el.click()")
                return sel
        except Exception:
            continue
    return ""


def click_greet_button_and_dismiss_modal(frame, page):
    """
    右侧栏打招呼为 <button class="... btn-greet">；点击后弹窗点「知道了」。
    列表里常有多个隐藏卡片上的 .btn-greet，禁止仅用 .first，必须在可见侧栏内点击。
    """
    max_attempts = 3
    hit = False
    for attempt in range(max_attempts):
        hit_hint = _click_visible_greet_in_sidebar_js(frame)
        if not hit_hint:
            hit_hint = _click_scoped_greet_playwright(frame)
        if hit_hint:
            print(f"    [OK] 已触发打招呼点击 ({hit_hint}, 第{attempt + 1}次)")
            hit = True
        else:
            print(f"    [WARN] 未命中可见打招呼按钮，重试 {attempt + 1}/{max_attempts}")
            time.sleep(1.0)
            continue

        time.sleep(0.8)
        if _greet_feedback_visible(frame, page):
            break
        print(f"    [WARN] 未检测到招呼弹窗，可能点到隐藏按钮，重试 {attempt + 1}/{max_attempts}")
        time.sleep(1.2)

    if not hit:
        raise RuntimeError("未找到当前侧栏可见的打招呼按钮")

    if not _greet_feedback_visible(frame, page):
        time.sleep(2.0)
    if not _greet_feedback_visible(frame, page):
        raise RuntimeError("打招呼后未出现「已向牛人发送招呼」等弹层，可能未真正发出")

    know_selectors = [
        'button:has-text("知道了")',
        'div.btn:has-text("知道了")',
        'text=知道了',
    ]
    dismissed = False
    for _ in range(25):
        for ctx_name, ctx in (("frame", frame), ("page", page)):
            if ctx is None:
                continue
            for ks in know_selectors:
                try:
                    kb = ctx.locator(ks).first
                    if kb.is_visible(timeout=600):
                        try:
                            kb.click(timeout=12000, force=True)
                        except Exception:
                            kb.evaluate("el => el.click()")
                        print(f"    [OK] 已关闭招呼弹窗：知道了 ({ctx_name})")
                        dismissed = True
                        time.sleep(0.7)
                        break
                except Exception:
                    continue
            if dismissed:
                break
        if dismissed:
            break
        time.sleep(0.35)

    if not dismissed:
        print("    [WARN] 未点到「知道了」，请手动关闭弹窗；将继续下一张卡片")

    try:
        page.keyboard.press("Escape")
        time.sleep(0.4)
        page.keyboard.press("Escape")
    except Exception:
        pass


_RESUME_SUMMARY_PRIMARY_JS = r"""
() => {
    const el = document.querySelector('.resume-summary');
    if (!el || !el.offsetParent) return '';
    return el.innerText.trim();
}
"""


def _evaluate_resume_summary_primary(ctx) -> str:
    """优先读取右侧栏 .resume-summary（经历概览 + ul.jobs）。"""
    if ctx is None:
        return ""
    try:
        out = ctx.evaluate(_RESUME_SUMMARY_PRIMARY_JS)
        return (out or "").strip()
    except Exception:
        pass
    try:
        out = ctx.locator("body").first.evaluate(_RESUME_SUMMARY_PRIMARY_JS)
        return (out or "").strip()
    except Exception:
        return ""


_RESUME_SCRAPE_JS = r"""
() => {
    const visible = (el) => el && el.offsetParent !== null;
    const text = (sel) => {
        const el = document.querySelector(sel);
        return visible(el) ? el.innerText.trim() : '';
    };
    let best = '';
    const upd = (t) => {
        if (t && t.length > best.length) best = t;
    };
    const summary = text('.resume-summary');
    if (summary.length > 40) upd(summary);
    const strictSelectors = [
        '.resume-summary ul.jobs:not(.education)',
        '.resume-summary ul.jobs.education',
        '.dialog-lib-resume',
        '.resume-summary',
        '.resume-detail',
        '[class*="detail-resume"]',
        '[class*="resume-detail"]',
        '[class*="resume-drawer"]',
        '[class*="ResumeDrawer"]',
        '[class*="geek-resume"]',
        '[class*="work-content"]',
        '[class*="work-desc"]',
        '[class*="job-content"]',
        '[class*="project-exp"]',
        '[class*="experience"]',
        '[class*="timeline"]',
        '[class*="overview"]',
        '[class*="work-list"]',
        '[class*="exp-item"]',
        '.geek-resume-wrap',
        '.resume-content',
        '.resume-scroll-content',
    ];
    strictSelectors.forEach((sel) => upd(text(sel)));
    const panel = text('.dialog-lib-resume');
    if (panel.length > 120) return panel;
    const blocks = [];
    const seen = new Set();
    const push = (t) => {
        if (!t || t.length < 15) return;
        const key = t.slice(0, 80);
        if (seen.has(key)) return;
        seen.add(key);
        blocks.push(t);
    };
    strictSelectors.forEach((sel) => push(text(sel)));
    if (blocks.length) upd(blocks.join('\n\n----------\n\n'));
    document.querySelectorAll(
        'div[class*="resume"], section[class*="resume"], div[class*="Resume"], ' +
        'div[class*="drawer"], div[class*="Drawer"], div[class*="detail"]'
    ).forEach((el) => {
        if (!visible(el)) return;
        const t = el.innerText.trim();
        if (t.length > best.length && t.length > 50) best = t;
    });
    return best;
}
"""


def _evaluate_resume_js(ctx):
    """
    ctx 可为 Page、Frame，或 iframe 的 FrameLocator。
    FrameLocator 无 evaluate，需通过 locator('body') 在子帧 document 里执行脚本。
    """
    if ctx is None:
        return ""
    try:
        out = ctx.evaluate(_RESUME_SCRAPE_JS)
        return (out or "").strip()
    except Exception:
        pass
    try:
        out = ctx.locator("body").first.evaluate(_RESUME_SCRAPE_JS)
        return (out or "").strip()
    except Exception:
        return ""


def collect_online_resume_text(frame, page=None) -> str:
    """
    抓取在线简历可读全文：iframe 内 + 顶层页（抽屉有时挂在主 document）。
    """
    candidates = []
    sp_f = _evaluate_resume_summary_primary(frame)
    if sp_f:
        candidates.append(sp_f)
    if page is not None:
        sp_p = _evaluate_resume_summary_primary(page)
        if sp_p:
            candidates.append(sp_p)
    t_frame = _evaluate_resume_js(frame)
    if t_frame:
        candidates.append(t_frame)
    if page is not None:
        t_page = _evaluate_resume_js(page)
        if t_page:
            candidates.append(t_page)
    hunt = collect_sidebar_hunt_text(frame, page)
    if hunt:
        candidates.append(hunt)
    if not candidates:
        return ""
    return max(candidates, key=len)


def collect_online_resume_text_all_zhipin_frames(page) -> str:
    """遍历 page.frames：侧栏偶尔不在 recommendFrame 内。"""
    if page is None:
        return ""
    best = ""
    frames = getattr(page, "frames", None) or []
    for fr in frames:
        try:
            url = fr.url or ""
            if url and "zhipin.com" not in url and "about:blank" not in url.lower():
                continue
            t = max(
                _evaluate_resume_summary_primary(fr),
                _evaluate_resume_js(fr),
                _evaluate_custom_js(fr, _SIDEBAR_HUNT_JS),
                key=len,
            )
            if len(t) > len(best):
                best = t
        except Exception:
            continue
    return best


def page_has_resume_canvas(page) -> bool:
    """Boss 新版在线简历中间区域常为 canvas#resume（像素绘制，无法 DOM 取词）。"""
    if page is None:
        return False
    js = """() => {
        const cv = document.querySelector('canvas#resume');
        if (cv && cv.getBoundingClientRect && cv.getBoundingClientRect().width > 50) return true;
        return !!document.querySelector('iframe[src*="resume"]');
    }"""
    try:
        if page.evaluate(js):
            return True
    except Exception:
        pass
    for fr in getattr(page, "frames", None) or []:
        try:
            if fr.evaluate(js):
                return True
        except Exception:
            continue
    return False


def _is_probably_binary_payload(s: str) -> bool:
    t = (s or "").strip()
    if len(t) < 120:
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return False
    return bool(re.match(r"^[A-Za-z0-9+/=\s]+$", t))


def text_from_resume_wapi_json(data, max_chars=14000) -> str:
    """从 Boss wapi 返回的 JSON 中抽取可读简历段落（结构随版本变化，做宽松遍历）。"""
    if not isinstance(data, dict) or data.get("code") != 0:
        return ""
    root = data.get("zpData") if isinstance(data.get("zpData"), (dict, list)) else data
    chunks = []
    hint_keys = (
        "desc",
        "content",
        "duty",
        "work",
        "project",
        "edu",
        "school",
        "advantage",
        "summary",
        "detail",
        "performance",
        "responsibility",
        "position",
        "company",
        "name",
        "major",
    )

    def walk(o, depth=0):
        if depth > 14 or len(chunks) > 400:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                ks = str(k).lower()
                if isinstance(v, str) and len(v.strip()) > 12:
                    vs = v.strip()
                    if _is_probably_binary_payload(vs):
                        continue
                    if any(h in ks for h in hint_keys):
                        chunks.append(vs)
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for it in o[:100]:
                walk(it, depth + 1)

    walk(root)
    out = "\n".join(dict.fromkeys(chunks))
    return out[:max_chars] if out else ""


def strip_binary_resume_noise(text: str) -> str:
    """去掉 wapi/接口里混入的长 Base64 串，避免淹没正文与时间线。"""
    lines_out = []
    for line in (text or "").splitlines():
        if _is_probably_binary_payload(line):
            continue
        lines_out.append(line)
    return "\n".join(lines_out).strip()


def wapi_json_regex_timeline(data) -> str:
    """从整份 JSON 字符串里抽取 YYYY.MM - YYYY.MM / 至今（结构未知时的兜底）。"""
    if not isinstance(data, dict):
        return ""
    try:
        s = json.dumps(data, ensure_ascii=False)
    except Exception:
        return ""
    found = re.findall(
        r"\d{4}\.\d{2}\s*[-－]\s*(?:\d{4}\.\d{2}|至今|现在|在职)", s
    )
    return "\n".join(dict.fromkeys(found))


def wapi_json_work_timeline_text(data) -> str:
    """从 zpData 结构里尝试捞出工作经历行（公司/职位/起止）。"""
    if not isinstance(data, dict) or data.get("code") != 0:
        return ""
    lines = []
    root = data.get("zpData") if isinstance(data.get("zpData"), (dict, list)) else data

    def emit(d):
        if not isinstance(d, dict):
            return
        company = (
            d.get("companyName")
            or d.get("company")
            or d.get("comName")
            or d.get("brandName")
            or ""
        )
        position = (
            d.get("positionName")
            or d.get("position")
            or d.get("jobName")
            or d.get("name")
            or ""
        )
        st = (
            d.get("startDate")
            or d.get("startTime")
            or d.get("beginDate")
            or d.get("startYearMon")
            or d.get("stime")
            or ""
        )
        et = (
            d.get("endDate")
            or d.get("endTime")
            or d.get("endYearMon")
            or d.get("etime")
            or ""
        )
        if company or position or st or et:
            lines.append(f"{company} {position} {st}-{et}".strip())

    def walk(o, depth=0):
        if depth > 20:
            return
        if isinstance(o, dict):
            keys = {str(k).lower() for k in o.keys()}
            if ("companyname" in keys or "company" in keys) and (
                "startdate" in keys or "startyearmon" in keys or "stime" in keys
            ):
                emit(o)
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for it in o[:120]:
                walk(it, depth + 1)

    walk(root)
    return "\n".join(lines[:80])


def wapi_bucket_work_timeline(bucket: list) -> str:
    if not bucket:
        return ""
    parts = []
    for item in bucket:
        if not isinstance(item, dict):
            continue
        t1 = wapi_json_work_timeline_text(item)
        t2 = wapi_json_regex_timeline(item)
        if t1:
            parts.append(t1)
        if t2:
            parts.append(t2)
    return "\n".join(parts).strip()


_SIDEBAR_HUNT_JS = r"""
() => {
    let best = '';
    const nodes = document.querySelectorAll('div,section,aside,main,article');
    for (const el of nodes) {
        if (!el.offsetParent) continue;
        const t = (el.innerText || '').trim();
        if (!t || t.length > 120000) continue;
        if (t.includes('经历概览')) {
            if (t.length > best.length) best = t;
            continue;
        }
        if (/\d{4}\.\d{2}\s*[-－]\s*(?:\d{4}\.\d{2}|至今|现在|在职)/.test(t) &&
            (t.includes('打招呼') || t.includes('收藏') || t.includes('转发'))) {
            if (t.length > best.length) best = t;
        }
    }
    return best;
}
"""


def _evaluate_custom_js(ctx, js: str) -> str:
    if ctx is None:
        return ""
    try:
        out = ctx.evaluate(js)
        return (out or "").strip()
    except Exception:
        pass
    try:
        out = ctx.locator("body").first.evaluate(js)
        return (out or "").strip()
    except Exception:
        return ""


def collect_sidebar_hunt_text(frame, page=None) -> str:
    """DOM 类名多变时：扫描含「经历概览」或侧栏日期条的容器。"""
    best = ""
    for ctx in (frame, page):
        t = _evaluate_custom_js(ctx, _SIDEBAR_HUNT_JS)
        if len(t) > len(best):
            best = t
    if page is None:
        return best
    for fr in getattr(page, "frames", None) or []:
        try:
            url = fr.url or ""
            if url and "zhipin.com" not in url and "about:blank" not in url.lower():
                continue
            t = _evaluate_custom_js(fr, _SIDEBAR_HUNT_JS)
            if len(t) > len(best):
                best = t
        except Exception:
            continue
    return best


class _ResumeWapiSniffer:
    """点击卡片后短时间窗口内收集可能含简历正文的 wapi JSON。"""

    def __init__(self, page):
        self._page = page
        self._bucket = []
        self._handler = None
        self._t0 = 0.0

    def _on_response(self, response):
        try:
            if time.time() - self._t0 > 35:
                return
            url = response.url or ""
            if "zhipin.com" not in url or "/wapi/" not in url:
                return
            ul = url.lower()
            if not any(
                k in ul
                for k in (
                    "resume",
                    "geek",
                    "relation",
                    "detail",
                    "online",
                    "expect",
                    "work",
                )
            ):
                return
            body = response.body()
            if not body or len(body) > 2_000_000:
                return
            data = json.loads(body.decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and data.get("code") == 0:
                self._bucket.append(data)
        except Exception:
            pass

    def start(self):
        self.clear()
        self._t0 = time.time()
        self._handler = self._on_response
        self._page.on("response", self._handler)

    def clear(self):
        self._bucket = []

    def stop(self):
        if self._handler is None:
            return
        try:
            self._page.off("response", self._handler)
        except Exception:
            pass
        self._handler = None

    def merged_text(self) -> str:
        best = ""
        for item in self._bucket:
            t = text_from_resume_wapi_json(item)
            if len(t) > len(best):
                best = t
        return best


def augment_resume_with_card_if_short(resume_text: str, card_text: str) -> str:
    """
    Canvas 简历场景下侧栏很短：把卡片上的「优势」等拼入，避免 LLM 只能看到时间线。
    """
    s = (resume_text or "").strip()
    c = (card_text or "").strip()
    if len(c) < 200:
        return s
    if len(s) >= MIN_RESUME_TEXT_LEN_FOR_LLM:
        return s
    extra = f"\n\n----------\n【牛人卡片原文（含优势等，Canvas 正文无法从 DOM 读取）】\n{c}"
    return (s + extra).strip()


def poll_resume_after_card_click(frame, page, min_chars=40):
    """
    点击卡片后轮询直至采到足够文本或超时（避免侧栏/API 未返回就抓取）。
    """
    deadline = time.time() + RESUME_PANEL_POLL_TIMEOUT_SEC
    best = ""
    while time.time() < deadline:
        t = collect_online_resume_text(frame, page)
        if len(t) > len(best):
            best = t
            if len(best) >= min_chars:
                break
        t_all = collect_online_resume_text_all_zhipin_frames(page)
        if len(t_all) > len(best):
            best = t_all
            if len(best) >= min_chars:
                break
        time.sleep(0.45)
    if len(best) < min_chars:
        best = collect_online_resume_text_all_zhipin_frames(page)
        if len(best) < min_chars:
            best = collect_online_resume_text(frame, page)
    return best


def get_seen_candidates():
    """加载已看过的候选人名单"""
    if SEEN_CANDIDATES_FILE.exists():
        try:
            with open(SEEN_CANDIDATES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            pass
    return set()


def mark_candidate_seen(candidate_name):
    """标记候选人已看过"""
    seen = get_seen_candidates()
    seen.add(candidate_name)
    with open(SEEN_CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)


def parse_llm_json_response(raw: str) -> dict:
    """
    MiniMax-M2.x 常在正文前附带 <think>...</think>，不能直接 json.loads。
    去掉推理块后取首个 JSON 对象解析。
    """
    s = (raw or "").strip()
    s = re.sub(
        r"<think>[\s\S]*?</think>",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
    if s.endswith("```"):
        s = s.rsplit("\n", 1)[0]
    s = s.strip()
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            s = s[i : j + 1]
    return json.loads(s)


def log_audit(candidate_name, card_text, summary_text, result):
    """追加审计日志到 jsonl 文件"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "candidate_name": candidate_name,
        "card_text": card_text[:500] if card_text else "",
        "summary_text": summary_text[:2000] if summary_text else "",
        "llm_result": result
    }
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def evaluate_resume(candidate_name, card_text, summary_text, max_age=None):
    """
    规则引擎判定（卡片 + 侧栏经历概览），不再调用 LLM。
    返回: {"is_match": bool, "score": int, "reason": str}
    """
    print(f"\n    [RULE] 规则评估: {candidate_name}")

    if max_age is None:
        max_age = get_filter_config()["max_age"]

    seen = get_seen_candidates()
    if candidate_name in seen:
        print(f"    [RULE] 命中去重，跳过: {candidate_name}")
        dup_r = {"is_match": False, "score": 0, "reason": "重复候选人"}
        log_audit(candidate_name, card_text, summary_text, dup_r)
        append_rule_report(
            candidate_name, card_text, summary_text, dup_r, tag="去重跳过"
        )
        return dup_r

    hdr_reject, hdr_reason = card_header_gate(card_text or "", max_age)
    if hdr_reject:
        result = {"is_match": False, "score": 0, "reason": hdr_reason}
        print(f"    [RULE] 硬过滤: {hdr_reason}")
        log_audit(candidate_name, card_text, summary_text, result)
        append_rule_report(
            candidate_name, card_text, summary_text, result, tag="侧栏打开后硬过滤"
        )
        mark_candidate_seen(candidate_name)
        return result

    result = rule_based_match(card_text or "", summary_text or "")

    log_audit(candidate_name, card_text, summary_text, result)
    mark_candidate_seen(candidate_name)

    print(f"    [RULE] 判定结果: is_match={result.get('is_match')}, score={result.get('score')}, reason={result.get('reason')}")
    if result.get("is_match"):
        seo_list = result.get("matched_seo_keywords") or []
        kw_list = result.get("matched_combined_keywords") or []
        gap_note_d = result.get("gap_note") or ""
        formula = result.get("score_formula") or ""
        gm = result.get("gap_months")
        if gm == 0:
            tail_2 = "最近一段为在职/至今，不校验「结束不早于某年月」"
        else:
            tail_2 = (
                f"最近一段已结束则须≥{RULE_MIN_LAST_JOB_END[0]}.{RULE_MIN_LAST_JOB_END[1]:02d}（本条已通过）"
            )
        print(
            f"    [RULE] 条件说明: ①有时间线且空窗≤{RULE_MAX_GAP_MONTHS}个月；②{tail_2}"
        )
        seo_join = ", ".join(seo_list[:30]) + (" …" if len(seo_list) > 30 else "")
        kw_join = ", ".join(kw_list[:40]) + (" …" if len(kw_list) > 40 else "")
        print(f"    [RULE] SEO词库命中({len(seo_list)}): [{seo_join}]")
        print(f"    [RULE] 综合词库命中({len(kw_list)}): [{kw_join}]")
        print(f"    [RULE] 经历摘要: {gap_note_d}")
        print(f"    [RULE] 分值计算: {formula}")
    append_rule_report(
        candidate_name, card_text, summary_text, result, tag="规则引擎"
    )
    return result

FIXED_SEED = "boss-recruit-agent-2026"

# 默认匹配关键词（业务结果 + SEO能力 + 运营经验）
DEFAULT_KEYWORDS = [
    # 业务结果关键词
    "询盘", "转化率", "曝光", "点击", "流量", "排名提升",
    # SEO专业能力关键词
    "关键词研究", "内链", "外链", "内容策略", "E-A-T", "技术SEO", "页面优化",
    # 网站运营经验关键词
    "独立站", "B端", "建站", "运营", "竞品分析", "SEMrush",
    # 工具信号
    "Google Analytics", "GA", "Search Console", "Ahrefs", "SEMrush"
]

# 年龄限制
MAX_AGE = 32

def get_profile_dir():
    """获取 Firefox profile 目录（与 login.py 共享）"""
    profile_dir = Path(__file__).parent.parent / "recruit_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir

def check_login(page) -> bool:
    """DOM 元素检测登录状态"""
    try:
        selectors = [".user-name", ".header-user-avatar"]
        for sel in selectors:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el:
                    text = el.inner_text().strip() if el.inner_text() else ""
                    if len(text) > 0:
                        return True
            except:
                continue
        return False
    except:
        return False

def get_filter_config():
    """获取筛选配置：关键词 + 年龄限制"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                return {
                    "keywords": config.get("filter_keywords", DEFAULT_KEYWORDS),
                    "max_age": config.get("max_age", MAX_AGE)
                }
        except:
            pass
    return {"keywords": DEFAULT_KEYWORDS, "max_age": MAX_AGE}

def get_resume_text(page, talent_card):
    """从牛人卡片获取在线简历内容"""
    try:
        # 点击牛人卡片打开侧边栏/详情
        talent_card.click()
        time.sleep(2)

        # 等待简历内容加载
        resume_selectors = [
            ".resume-content",
            ".candidate-resume",
            "[class*='resume']",
            "[class*='experience']",
            ".work-history",
        ]

        resume_text = ""
        for selector in resume_selectors:
            try:
                elem = page.query_selector(selector)
                if elem:
                    resume_text = elem.inner_text()
                    if resume_text and len(resume_text) > 50:
                        break
            except:
                pass

        # 关闭详情（如果有关闭按钮）
        try:
            close_btn = page.query_selector(".close-btn")
            if close_btn:
                close_btn.click()
                time.sleep(0.5)
        except:
            pass

        return resume_text

    except Exception as e:
        print(f"    [WARN] 获取简历失败: {e}")
        return ""

def extract_age(resume_text):
    """从简历文本中提取年龄"""
    import re
    # 常见模式：32岁、32 years old、年龄32
    patterns = [
        r'(\d{2})岁',
        r'年龄[：:]*(\d{2})',
        r'(\d{2})\s*years?\s*old',
    ]
    for pattern in patterns:
        match = re.search(pattern, resume_text)
        if match:
            return int(match.group(1))
    return None

def analyze_resume_match(resume_text, keywords, max_age=32):
    """
    分析简历是否匹配筛选条件
    1. 关键词匹配（业务结果 + SEO能力 + 运营经验）
    2. 年龄限制（32岁以下）
    """
    if not resume_text:
        return {"match": False, "reason": "无法获取简历内容"}

    resume_lower = resume_text.lower()
    matched = []

    # 1. 关键词匹配
    for kw in keywords:
        if kw.lower() in resume_lower:
            matched.append(kw)

    # 2. 年龄检查
    age = extract_age(resume_text)
    age_ok = True
    if age is not None and age > max_age:
        age_ok = False
        matched.append(f"年龄{age}岁(超过{max_age}岁)")

    # 判断是否匹配
    # 需要同时满足：有关键词匹配 AND (无年龄信息 或 年龄符合)
    has_keyword_match = any(kw in ["SEO", "Google", "搜索引擎", "SEM", "AdWords", "数据分析",
                                    "询盘", "转化率", "曝光", "流量", "排名提升",
                                    "独立站", "B端", "建站", "运营", "SEMrush"]
                            for kw in matched)

    if has_keyword_match and age_ok:
        return {
            "match": True,
            "reason": f"匹配: {', '.join(matched[:5])}" + (f", 年龄{age}岁" if age else "")
        }
    elif not age_ok:
        return {
            "match": False,
            "reason": f"年龄{age}岁超过{max_age}岁限制"
        }
    else:
        return {
            "match": False,
            "reason": "简历中未找到相关关键词"
        }

def greet(talent_names=None, top=None, write_report=True, argv_summary=""):
    """
    打招呼前筛选 + 发送

    使用 persistent_context + 固定 seed，扫码一次永久免登。
    top: 本轮最多成功打招呼的人数；默认 DEFAULT_GREET_TOP（20）。不匹配的人会跳过，继续扫下一张直到凑满。
    write_report: 是否在项目 reports/ 下写入本轮判定 txt（环境变量 BOSS_GREET_NO_REPORT=1 等价关闭）。
    """
    if top is None:
        top = DEFAULT_GREET_TOP

    print("[START] Greeting filter begins...")

    no_report_env = os.environ.get("BOSS_GREET_NO_REPORT", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if write_report and not no_report_env:
        init_rule_report_session(top, argv_summary=argv_summary)
    else:
        disable_rule_report_session()

    filter_config = get_filter_config()
    keywords = filter_config["keywords"]
    max_age = filter_config["max_age"]
    print(
        f"[CONFIG] Filter: keywords={keywords}, max_age={max_age}岁, "
        f"rule_min_job_end={RULE_MIN_LAST_JOB_END[0]}.{RULE_MIN_LAST_JOB_END[1]:02d}, "
        f"max_gap_months={RULE_MAX_GAP_MONTHS}, "
        f"本轮成功打招呼上限 top={top}"
    )

    # 预生成固定指纹
    random.seed(FIXED_SEED)
    from camoufox.fingerprints import generate_fingerprint
    fp = generate_fingerprint()
    print(f"[fp] UA: {fp.navigator.userAgent[:70]}...")

    opts = launch_options(
        headless=False,
        humanize=True,
        fingerprint=fp,
        window_size=(1280, 720),
        i_know_what_im_doing=True,
    )
    opts.update({
        "persistent_context": True,
        "user_data_dir": str(get_profile_dir().resolve()),
    })

    # Monkey-patch NewBrowser 支持 persistent_context
    import camoufox.sync_api as sync_api
    _original = sync_api.NewBrowser
    def patched(playwright, *, headless=None, from_options=None, persistent_context=False, debug=None, **kwargs):
        if from_options and from_options.get('persistent_context'):
            from_options = {k: v for k, v in from_options.items()
                           if k not in ('persistent_context', 'fingerprint', 'humanize', 'geoip',
                                        'os', 'block_images', 'i_know_what_im_doing', 'seed',
                                        'window_size', 'debug')}
            context = playwright.firefox.launch_persistent_context(**from_options)
            return context
        return _original(playwright, headless=headless, from_options=from_options,
                         persistent_context=persistent_context, debug=debug, **kwargs)
    sync_api.NewBrowser = patched

    browser = Camoufox(from_options=opts)
    context = browser.__enter__()
    page = context.pages[0] if context.pages else context.new_page()

    greeted = []
    greet_count = 0
    stopped_note = ""

    try:
        # 进入推荐牛人页面
        page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded")
        time.sleep(2)

        # 检查登录状态（DOM 检测）
        if not check_login(page):
            print("[login] Not logged in, going to login page...")
            page.goto("https://www.zhipin.com/web/geek/login", wait_until="domcontentloaded")
            # 等待扫码登录
            max_wait, waited = 180, 0
            while waited < max_wait:
                time.sleep(2)
                waited += 2
                url = page.url or ""
                if "_security_check" in url or "security-check" in url:
                    print(f"[login] Security check, waiting 10s...")
                    time.sleep(10)
                    continue
                if check_login(page):
                    print(f"[OK] Login success! URL: {page.url}")
                    break
                if waited % 20 == 0:
                    print(f"[login] {waited}s - URL: {url[:60]}")
            else:
                print("[FAIL] Login timeout")
                stopped_note = "登录超时"
                return greeted
        else:
            print("[login] Already logged in (profile persists)")

        # === 步骤1: 关闭广告弹窗 ===
        try:
            time.sleep(2)
            close_selectors = [".dialog-close", "[class*='modal'] [class*='close']", "[class*='dialog'] .icon-close"]
            for sel in close_selectors:
                close_btn = page.query_selector(sel)
                if close_btn:
                    close_btn.click()
                    print("[filter] Ad modal closed")
                    time.sleep(1)
                    break
        except Exception as e:
            print(f"[filter] No ad modal or close failed: {e}")

        # === 步骤2: 点击左侧"推荐牛人" ===
        try:
            time.sleep(2)
            menu_selectors = [
                "text=推荐牛人",
                "[class*='sidebar'] text=推荐牛人",
                ".nav-list text=推荐牛人",
            ]
            for sel in menu_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn and btn.is_visible():
                        btn.click()
                        print("[filter] Clicked 'Recommend Talent' in sidebar")
                        time.sleep(3)
                        break
                except:
                    continue
        except Exception as e:
            print(f"[filter] Sidebar click failed: {e}")

        # === 穿透 iframe: recommendFrame ===
        time.sleep(3)  # 等待 iframe 加载
        frame = None
        try:
            frame = page.frame_locator("iframe[name='recommendFrame']")
            frame.locator(".card-inner").first.wait_for(state="visible", timeout=15000)
            print("[talent] iframe found, cards visible")
            apply_recommend_talent_filters(page, frame)
        except Exception as e:
            print(f"[talent] iframe or cards not found: {e}")

        card_index = 0
        last_count_after_list_scroll = -1
        list_scroll_stall = 0
        wapi_sniffer = _ResumeWapiSniffer(page)
        print(
            f"[talent] 目标成功打招呼 {top} 人：将遍历卡片并在 iframe 内滚动加载更多，"
            f"直至凑满或连续 {GREET_LIST_SCROLL_STALL_MAX} 次滚动仍无新卡片"
        )
        while greet_count < top:
            if frame is None:
                break

            try:
                # 在 iframe 内获取牛人卡片
                cards = frame.locator(".card-inner")
                card_count = cards.count()

                if card_count == 0:
                    for _ in range(4):
                        scroll_recommend_list(frame, page)
                        cards = frame.locator(".card-inner")
                        card_count = cards.count()
                        if card_count > 0:
                            break
                    if card_count == 0:
                        print("[talent] iframe 内滚动后仍无卡片，结束任务")
                        stopped_note = (
                            stopped_note or "推荐列表无卡片，未满 top"
                        )
                        break

                if card_index >= card_count:
                    scroll_recommend_list(frame, page)
                    cards = frame.locator(".card-inner")
                    new_count = cards.count()
                    if (
                        last_count_after_list_scroll >= 0
                        and new_count == last_count_after_list_scroll
                    ):
                        list_scroll_stall += 1
                        print(
                            f"[talent] 列表滚到底后仍为 {new_count} 张，"
                            f"无新增 ({list_scroll_stall}/{GREET_LIST_SCROLL_STALL_MAX})"
                        )
                    else:
                        list_scroll_stall = 0
                    last_count_after_list_scroll = new_count
                    if list_scroll_stall >= GREET_LIST_SCROLL_STALL_MAX:
                        print(
                            "[talent] 已达滚动停滞上限（Boss 暂无更多推荐或需换筛选），"
                            f"当前成功打招呼 {greet_count}/{top}"
                        )
                        stopped_note = stopped_note or (
                            f"列表滚动无新卡片，未满 top={top}（已打招呼 {greet_count}）"
                        )
                        break
                    card_index = 0
                    continue

                card = cards.nth(card_index)

                # 获取牛人信息
                try:
                    card_text = card.text_content()
                    lines = [l.strip() for l in card_text.split('\n') if l.strip()]
                    name = lines[0] if lines else "未知"
                    title = lines[1] if len(lines) > 1 else "未知"
                    print(f"    [CARD] {name} | {title}")
                except Exception as e:
                    print(f"    [WARN] Failed to get card info: {e}")
                    card_index += 1
                    continue

                hdr_reject, hdr_reason = card_header_gate(card_text, max_age)
                if hdr_reject:
                    r = {"is_match": False, "score": 0, "reason": hdr_reason}
                    print(f"    [FILTER] {hdr_reason}（不调LLM，未打开简历侧栏）")
                    log_audit(name, card_text, "", r)
                    append_rule_report(name, card_text, "", r, tag="卡片硬过滤")
                    mark_candidate_seen(name)
                    card_index += 1
                    time.sleep(5)
                    continue

                # 点击卡片打开简历（Boss 中间区域常为 canvas#resume，DOM 无正文，依赖 wapi + 卡片摘要）
                try:
                    wapi_sniffer.stop()
                    if CAPTURE_RESUME_WAPI:
                        wapi_sniffer.start()
                    card.evaluate("el => el.click()")
                    print(
                        "    [INFO] Card clicked, polling resume panel "
                        f"(timeout={RESUME_PANEL_POLL_TIMEOUT_SEC}s)..."
                    )
                    time.sleep(POST_CARD_CLICK_PAUSE_SEC)
                except Exception as e:
                    print(f"    [WARN] Card click failed: {e}")
                    wapi_sniffer.stop()
                    card_index += 1
                    continue

                # 轮询 iframe + 顶层 + 所有 zhipin frame，避免侧栏未挂载就抓取导致长度为 0
                resume_text = ""
                try:
                    resume_text = poll_resume_after_card_click(frame, page, min_chars=40)
                    expand_resume_sections(frame)
                    time.sleep(0.85)
                    resume_text = max(
                        resume_text,
                        collect_online_resume_text(frame, page),
                        collect_online_resume_text_all_zhipin_frames(page),
                        key=len,
                    )
                    if CAPTURE_RESUME_WAPI:
                        wapi_txt = wapi_sniffer.merged_text()
                        wapi_sniffer.stop()
                        if len(wapi_txt) > len(resume_text):
                            resume_text = wapi_txt
                            print(f"    [RESUME] 已从 wapi 响应抽取更长正文: {len(resume_text)} 字")
                        elif wapi_txt:
                            resume_text = (resume_text + "\n\n----------\n【接口摘录】\n" + wapi_txt).strip()
                            print(f"    [RESUME] 已追加 wapi 摘录，总长 {len(resume_text)} 字")
                    else:
                        wapi_sniffer.stop()

                    resume_text = strip_binary_resume_noise(resume_text)
                    if CAPTURE_RESUME_WAPI:
                        tl = wapi_bucket_work_timeline(wapi_sniffer._bucket)
                        if tl:
                            resume_text = (
                                "【wapi工作时间线】\n" + tl + "\n\n" + resume_text
                            ).strip()
                            print(f"    [RESUME] 已从 wapi 解析工作时间线（+{len(tl)} 字，置于文首便于解析最近一段）")

                    if page_has_resume_canvas(page):
                        print(
                            "    [RESUME] 当前简历页含 canvas#resume：中间工作经历为画布绘制，"
                            "无法通过 DOM 复制；已用侧栏 + wapi（若有）+ 下方卡片摘要补强。"
                        )

                    resume_text = augment_resume_with_card_if_short(resume_text, card_text or "")
                    print(f"    [RESUME] 合并后全文长度: {len(resume_text)}")
                    print(f"    [RESUME] Content:\n{resume_text[:2500]}{'…' if len(resume_text) > 2500 else ''}")
                except Exception as e:
                    print(f"    [WARN] 抓取简历全文失败: {e}")
                    wapi_sniffer.stop()

                if resume_text:
                    print(f"    [RESUME] Got resume text, length: {len(resume_text)}")
                    result = evaluate_resume(name, card_text, resume_text, max_age=max_age)

                    if result.get("is_match"):
                        print(
                            f"    [RESULT] 是 - 匹配(score={result.get('score')}) - "
                            f"等待{GREET_AFTER_MATCH_WAIT_SEC:.0f}秒后点击打招呼"
                        )
                        time.sleep(GREET_AFTER_MATCH_WAIT_SEC)
                        try:
                            click_greet_button_and_dismiss_modal(frame, page)
                            greeted.append(
                                {
                                    "name": name,
                                    "title": title,
                                    "score": result.get("score"),
                                    "reason": result.get("reason"),
                                }
                            )
                            greet_count += 1
                        except Exception as e:
                            print(f"    [FAIL] Greet failed: {e}")
                    else:
                        print(f"    [RESULT] 否 - 不匹配(score={result.get('score')}): {result.get('reason')}")
                else:
                    print(
                        "    [WARN] No resume sidebar text, skip rules。"
                        "若侧栏已显示仍为0：检查 iframe / 选择器。"
                    )
                    nr = {"is_match": False, "score": 0, "reason": "无侧栏文本，无法规则校验"}
                    log_audit(name, card_text, "", nr)
                    append_rule_report(name, card_text, "", nr, tag="无侧栏文本")
                    mark_candidate_seen(name)

                # 每张卡片处理完等5秒防风控
                print("    [INFO] Waiting 5s before next card...")
                time.sleep(5)

                # 关闭简历弹窗
                try:
                    page.keyboard.press("Escape")
                    time.sleep(2)
                except:
                    pass

                card_index += 1
                time.sleep(2)

            except Exception as e:
                print(f"    [WARN] Loop error: {e}")
                card_index += 1
                continue

        print(f"\n[OK] Done! Greeted {len(greeted)} people")
        if len(greeted) < top:
            print(
                f"[INFO] 未满上限 top={top}：常见原因为匹配人数不足、"
                "或推荐列表滚动后仍无新卡片（详见 [talent] 日志与报告备注）"
            )

    except Exception as e:
        stopped_note = str(e)
        print(f"[FAIL] 执行失败: {e}")
    finally:
        finalize_rule_report_session(greeted, stopped_note)
        browser.__exit__(None, None, None)

    return greeted

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Boss 招聘方：推荐牛人列表规则筛选并可自动打招呼",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_GREET_TOP,
        metavar="N",
        help=(
            "本轮最多成功打招呼的人数（默认 %(default)s，可由环境变量 BOSS_GREET_TOP 覆盖）；"
            "会先跳过不匹配卡片，直到凑满 N 或列表用尽"
        ),
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="不写入 reports/greet_rule_report_*.txt",
    )
    args = parser.parse_args()
    argv_summary = " ".join(sys.argv[1:])

    greeted = greet(
        top=args.top,
        write_report=not args.no_report,
        argv_summary=argv_summary,
    )

    print(f"\n===JSON_START===")
    print(json.dumps(greeted, ensure_ascii=False, indent=2))
    print(f"===JSON_END===")