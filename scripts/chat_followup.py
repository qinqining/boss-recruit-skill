#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招聘方：沟通列表中目标状态会话的跟进（默认含通勤/经历/索要简历；可 --resume-only 只做要简历）。

- 与 greet.py 一致：Camoufox 持久化 profile（recruit_profile）、固定 seed、排除 UBO。
- 默认打开招聘端沟通页；若贵司实际入口不同，设环境变量 BOSS_RECRUIT_CHAT_URL。
- 列表匹配：BOSS_FOLLOWUP_ROW_TEXT 支持多文案（逗号、|、； 分隔）。**新版 Web 沟通页常无「继续沟通」**，`--resume-only` 时可能仍用 `[送达]`/`[已读]` 等词去**匹配到行**；默认对**归类为送达/已读**（预览任意位置含 `[送达]`/`[已读]`）的行**不发跟进**。要恢复旧逻辑：`BOSS_FOLLOWUP_SKIP_DELIVERED_READ=0`。
- 列表预览分三类：**送达/已读**（行首标签，默认跳过发消息）、**对方发起**（无行首 [送达]/[已读] 且多为候选人首句）、**简历待同意**（对方要发附件简历，脚本会尝试点「同意」）。未设 `BOSS_FOLLOWUP_ROW_TEXT` 且 `--resume-only` 时用宽列表扫描。
- 列表分区：可选 `BOSS_FOLLOWUP_LIST_TAB=沟通中` 或 `新招呼`；默认不切换（留在「全部」），避免误点导致列表为空。
- 调试：`py scripts/chat_followup.py -v --dry-run` 或设 `BOSS_FOLLOWUP_VERBOSE=1`。
- 留窗：`--dry-run` 或 `-v` 时默认会「按 Enter 再关浏览器」；正式无人值守请加 `--no-keep-open`。
- 导出 DOM 线索（无需 F12）：`py scripts/chat_followup.py --dump-dom` — 打开沟通页后先在页面里**点开目标会话**，回到终端**按 Enter** 再写入 `reports/followup_dom_hints_*.json`；第二次 Enter 关浏览器（`--no-keep-open` 时导出后立刻关）。
- 与 login 共用 `recruit_profile`：**不要**在另一 Camoufox 仍打开时启动本脚本，否则 `launch_persistent_context` 可能秒退；先关窗或关 `login_keep_open.py`。
- 列表右侧时间：默认**只处理**「今日」样式（如 `14:21`、`刚刚`、`3分钟前`）；**跳过**「昨天」「前天」及 `MM-DD` / `YYYY-MM-DD` / `M月D日」等。设 `BOSS_FOLLOWUP_LIST_TIME_CHECK=0` 关闭该过滤。
- 会话去重：`session_key` 优先用列表行上的 `data-geek-id` 等稳定属性；发消息后点左侧列表收回焦点；同一脚本运行内同一 key 只处理一次，避免仍停在同一聊天页时重复发。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
if _root not in sys.path:
    sys.path.insert(0, _root)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from boss_login_probe import probe_logged_in

from camoufox import Camoufox
from camoufox import launch_options
from camoufox.addons import DefaultAddons

FIXED_SEED = "boss-recruit-agent-2026"
SCRIPT_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = SCRIPT_DIR / "reports" / "followup_state.json"
CONFIG_PATH = SCRIPT_DIR / "config.json"
REPORTS_DIR = SCRIPT_DIR / "reports"

# 招聘方沟通页（与牛人端 /web/geek/chat 区分）
DEFAULT_CHAT_URL = os.environ.get(
    "BOSS_RECRUIT_CHAT_URL",
    "https://www.zhipin.com/web/boss/chat",
)
# 左列会话行上需包含的文案；支持多个，用英文逗号或 | 或 ； 分隔（任一条命中即视为目标行）
_FOLLOWUP_ROW_TEXT_RAW = os.environ.get("BOSS_FOLLOWUP_ROW_TEXT", "继续沟通").strip() or "继续沟通"


def _parse_row_markers(raw: str) -> List[str]:
    s = (raw or "").strip() or "继续沟通"
    for sep in ("|", "；", ";"):
        s = s.replace(sep, ",")
    return [x.strip() for x in s.split(",") if x.strip()]


FOLLOWUP_ROW_MARKERS: List[str] = _parse_row_markers(_FOLLOWUP_ROW_TEXT_RAW)
# 兼容旧日志变量名
FOLLOWUP_ROW_MARKER = FOLLOWUP_ROW_MARKERS[0] if FOLLOWUP_ROW_MARKERS else "继续沟通"
# 登录后再等几秒让左侧列表渲染（含 iframe 内）
FOLLOWUP_LIST_WAIT_SEC = float(os.environ.get("BOSS_FOLLOWUP_LIST_WAIT_SEC", "4"))
# 首屏后额外轮询列表（Boss SPA 列表常晚于顶栏）
FOLLOWUP_LIST_POLL_TRIES = max(1, int(os.environ.get("BOSS_FOLLOWUP_LIST_POLL_TRIES", "8")))
FOLLOWUP_LIST_POLL_STEP_SEC = float(os.environ.get("BOSS_FOLLOWUP_LIST_POLL_STEP_SEC", "1.25"))
# 点开会话后等待正文区（秒）
AFTER_OPEN_CHAT_SEC = float(os.environ.get("BOSS_FOLLOWUP_AFTER_OPEN_CHAT_SEC", "2.5"))
# 1 / true / yes → 与 --verbose 等价
_VERBOSE_ENV = os.environ.get("BOSS_FOLLOWUP_VERBOSE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "debug",
)


def _env_verbose() -> bool:
    return _VERBOSE_ENV


def _env_keep_open() -> bool:
    return os.environ.get("BOSS_FOLLOWUP_KEEP_OPEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _env_no_keep_open() -> bool:
    return os.environ.get("BOSS_FOLLOWUP_NO_KEEP_OPEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _user_set_boss_followup_row_text() -> bool:
    """用户是否显式设置过 BOSS_FOLLOWUP_ROW_TEXT（含设为空串）。"""
    return "BOSS_FOLLOWUP_ROW_TEXT" in os.environ


def _effective_row_markers(resume_eff: bool) -> List[str]:
    """
    最终用于列表 has_text 的文案列表。
    未显式设置 BOSS_FOLLOWUP_ROW_TEXT 且 resume_only 时，兼容当前 /web/chat/index 列表（[送达]/[已读] 等）。
    """
    base = list(FOLLOWUP_ROW_MARKERS or ["继续沟通"])
    if not resume_eff or _user_set_boss_followup_row_text():
        return base
    merged = _parse_row_markers(
        "[送达],[已读],对方愿发送,对方想发送,我们正在招聘,继续沟通"
    )
    out: List[str] = []
    for m in merged:
        if m and m not in out:
            out.append(m)
    return out


def _resolve_list_tab_label() -> str:
    """
    沟通页顶部分区（全部 / 新招呼 / 沟通中 …）要点击的标签文案。
    - 若环境变量 BOSS_FOLLOWUP_LIST_TAB 已设置（含空串）：用其 strip 结果，空串表示不点分区。
    - 若未设置：默认不点分区（留在「全部」；与当前 Web 版列表一致）。
    - 需要只看「沟通中」「新招呼」时再设：BOSS_FOLLOWUP_LIST_TAB=沟通中
    """
    if "BOSS_FOLLOWUP_LIST_TAB" in os.environ:
        return os.environ.get("BOSS_FOLLOWUP_LIST_TAB", "").strip()
    return ""


def _click_list_filter_tab(page, tab_label: str, *, verbose: bool) -> bool:
    """点击沟通列表上方的分区标签（如 沟通中、新招呼）。"""
    lbl = (tab_label or "").strip()
    if not lbl:
        return False
    sels = (
        '[role="tab"]',
        '[class*="filter-item"]',
        '[class*="tab-item"]',
        '[class*="top-tab"]',
        '[class*="TopTab"]',
        '[class*="list-tab"]',
        '[class*="chat-tab"]',
        '[class*="sub-tab"]',
    )
    for sel in sels:
        try:
            loc = page.locator(sel).filter(has_text=lbl).first
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=2000):
                loc.click(timeout=5000)
                time.sleep(1.2)
                print(f"[followup] 已点击列表分区「{lbl}」（选择器 {sel}）")
                if verbose:
                    print(f"[followup][dbg] tab 点击成功: {sel!r} + {lbl!r}")
                return True
        except Exception as ex:
            if verbose:
                print(f"[followup][dbg] 分区「{lbl}」尝试 {sel!r}: {ex!r}")
            continue
    print(
        f"[followup][WARN] 未点到分区「{lbl}」；仍在当前分区下匹配列表。"
        f"可检查页面标签文案或设 BOSS_FOLLOWUP_LIST_TAB=新招呼 等。"
    )
    return False


def get_profile_dir() -> Path:
    p = SCRIPT_DIR / "recruit_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _profile_lock_hint(profile: Path) -> Optional[str]:
    """若存在 profile 锁文件，多半仍有浏览器占用该目录，或上次异常退出未清锁。"""
    for name in ("parent.lock", ".parentlock"):
        lock = profile / name
        try:
            if lock.exists():
                return (
                    f"发现 {profile / name}：同一 recruit_profile 只能被一个 Camoufox 使用。"
                    "请先关掉所有用该目录的浏览器（含 login_keep_open / 其它终端里的脚本窗口）；"
                    "若已确认无进程，可手动删除上述锁文件后重试。"
                )
        except OSError:
            continue
    return None


def load_followup_config() -> Dict[str, Any]:
    """config.json 中可选 followup 段；缺省用环境变量。"""
    base: Dict[str, Any] = {
        "company_location": os.environ.get("BOSS_FOLLOWUP_LOCATION", "").strip()
        or "壹方天地B区（可在 config.json 的 followup.company_location 修改）",
        "commute_note": os.environ.get("BOSS_FOLLOWUP_COMMUTE_NOTE", "").strip(),
        "topic_keywords": [
            "SEO", "独立站", "Google", "Ads", "投放", "运营", "项目", "技术",
            "询盘", "转化", "外链", "内容",
        ],
        "pause_after_send_sec": float(os.environ.get("BOSS_FOLLOWUP_PAUSE_SEC", "6")),
        "resume_only": os.environ.get("BOSS_FOLLOWUP_RESUME_ONLY", "").strip().lower()
        in ("1", "true", "yes"),
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            fu = data.get("followup") or {}
            if isinstance(fu, dict):
                base.update({k: v for k, v in fu.items() if v is not None})
        except Exception:
            pass
    return base


def _today_key() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"sessions": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def session_key(name: str, preview: str, *, stable_row_id: str = "") -> str:
    """稳定 id 优先（行上 data-geek-id 等），避免发消息后列表预览变化导致同会话被当成新人。"""
    safe = re.sub(r"\s+", " ", (name or "未知").strip())[:40]
    sid = (stable_row_id or "").strip()
    if sid:
        h = hashlib.md5(f"{safe}|{sid}".encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{safe}|{h}"
    h = hashlib.md5(f"{safe}|{preview[:120]}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{safe}|{h}"


def guess_topic(snippet: str, keywords: List[str]) -> str:
    for kw in keywords:
        if kw and kw in snippet:
            return kw
    return "岗位相关"


def _looks_like_resume_received(blob: str) -> bool:
    """列表预览或聊天区内若已出现「对方已发简历」类表述，则不再索要。"""
    b = (blob or "").strip()
    if not b:
        return False
    needles = (
        "已发简历",
        "简历已发",
        "发了简历",
        "发您简历",
        "简历见附件",
        "附件是简历",
        "请查收简历",
        "这是我的简历",
        "简历发您了",
        "刚发了简历",
    )
    return any(x in b for x in needles)


# --- 沟通列表预览：三类（先做规则分类，后续可接不同话术/策略）---
ROW_KIND_RESUME_CONSENT = "resume_consent"  # 对方要发附件简历，需点「同意」
ROW_KIND_OUTBOUND_WAITING = "outbound_waiting"  # [送达]/[已读]：我方已发，等对方
ROW_KIND_CANDIDATE_INITIATED = "candidate_initiated"  # 对方发起（列表无送达/已读前缀）
ROW_KIND_UNKNOWN = "unknown"


def _row_kind_label_cn(kind: str) -> str:
    return {
        ROW_KIND_RESUME_CONSENT: "简历待同意（需点同意）",
        ROW_KIND_OUTBOUND_WAITING: "送达/已读（我方已发对方未回）",
        ROW_KIND_CANDIDATE_INITIATED: "对方发起沟通",
        ROW_KIND_UNKNOWN: "未分类",
    }.get(kind, kind)


def classify_row_preview(preview: str) -> str:
    """
    根据列表行副文案（预览）分为三类 + unknown。
    顺序：先简历待同意，再我方送达/已读，再对方发起。

    注意：preview 常为整行 inner_text（姓名、职位在前，「您好」在后的候选人首句），
    不能仅用 startswith("您好") 判断对方发起。
    """
    p = (preview or "").replace("\n", " ").strip()
    if len(p) < 4:
        return ROW_KIND_UNKNOWN
    if ("对方" in p or "附件" in p) and "简历" in p:
        if (
            "同意" in p
            or "是否" in p
            or "想发送" in p
            or "愿发送" in p
            or "发送附件" in p
            or "发附件" in p
        ):
            return ROW_KIND_RESUME_CONSENT
    if "[送达]" in p:
        return ROW_KIND_OUTBOUND_WAITING
    if "[已读]" in p:
        return ROW_KIND_OUTBOUND_WAITING
    if "[送达]" not in p and "[已读]" not in p:
        if p.startswith("您好") or p.startswith("你好"):
            return ROW_KIND_CANDIDATE_INITIATED
        # 姓名/职位在前的列表行：「您好，…」「你好，…」多出现在行中后部
        if re.search(r"您好[,，、\s]", p) or re.search(r"你好[,，、\s]", p):
            return ROW_KIND_CANDIDATE_INITIATED
        head = p[:96]
        if (
            "我对" in head
            or "感兴趣" in head
            or "应届生" in head
            or "一周" in head
            or "在吗" in head
            or "请问" in head
            or "方便沟通" in head
            or "看到招聘" in head
        ):
            return ROW_KIND_CANDIDATE_INITIATED
    return ROW_KIND_UNKNOWN


def build_message(
    cfg: Dict[str, Any],
    round_idx: int,
    candidate_name: str,
    chat_snippet: str,
    *,
    resume_only: bool = False,
) -> str:
    """round_idx: 本轮对该会话是第几条跟进（0=首条跟进）。resume_only 时固定为索要简历话术。"""
    resume = (
        "方便发一份最新简历（PDF 优先）吗？我这边转给用人部门同事一起做初筛，谢谢。"
    )
    if resume_only:
        msg = resume
        if candidate_name and candidate_name != "未知":
            msg = f"{candidate_name}您好，{msg}" if not msg.startswith(candidate_name) else msg
        return msg

    loc = cfg.get("company_location") or "贵司附近"
    note = (cfg.get("commute_note") or "").strip()
    topic = guess_topic(chat_snippet, list(cfg.get("topic_keywords") or []))
    commute = f"我们公司办公在「{loc}」"
    if note:
        commute += f"，{note}"
    commute += "，您通勤上可以接受吗？"

    ask_exp = (
        f"看到您简历/沟通里和「{topic}」比较相关，想多了解一点："
        f"最近一段里您主要负责哪一块？方便用两三句话说说吗？"
    )

    resume = (
        "方便发一份最新简历（PDF 优先）吗？我这边转给用人部门同事一起做初筛，谢谢。"
    )

    seq = [commute, ask_exp, resume]
    msg = seq[round_idx % len(seq)]
    # 轻微个性化
    if candidate_name and candidate_name != "未知":
        msg = f"{candidate_name}您好，{msg}" if not msg.startswith(candidate_name) else msg
    return msg


_PATCH_PERSISTENT_DONE = False


def _patch_persistent_context() -> None:
    global _PATCH_PERSISTENT_DONE
    if _PATCH_PERSISTENT_DONE:
        return
    import camoufox.sync_api as sync_api

    _original = sync_api.NewBrowser

    def patched(
        playwright,
        *,
        headless=None,
        from_options=None,
        persistent_context=False,
        debug=None,
        **kwargs,
    ):
        if from_options and from_options.get("persistent_context"):
            from_options = {
                k: v
                for k, v in from_options.items()
                if k
                not in (
                    "persistent_context",
                    "fingerprint",
                    "humanize",
                    "geoip",
                    "os",
                    "block_images",
                    "i_know_what_im_doing",
                    "seed",
                    "window_size",
                    "debug",
                )
            }
            return playwright.firefox.launch_persistent_context(**from_options)
        return _original(
            playwright,
            headless=headless,
            from_options=from_options,
            persistent_context=persistent_context,
            debug=debug,
            **kwargs,
        )

    sync_api.NewBrowser = patched
    _PATCH_PERSISTENT_DONE = True


def _launch_browser(*, verbose: bool = False):
    random.seed(FIXED_SEED)
    from camoufox.fingerprints import generate_fingerprint

    prof = get_profile_dir().resolve()
    print(f"[followup] 持久化 profile 目录: {prof}")
    hint = _profile_lock_hint(prof)
    if hint:
        print(f"[followup][WARN] {hint}")
    if verbose:
        print(f"[followup][dbg] FIXED_SEED={FIXED_SEED!r} STATE_FILE={STATE_FILE}")

    fp = generate_fingerprint()
    print(f"[fp] UA: {fp.navigator.userAgent[:70]}...")
    opts = launch_options(
        headless=False,
        humanize=True,
        fingerprint=fp,
        window_size=(1280, 720),
        i_know_what_im_doing=True,
        exclude_addons=[DefaultAddons.UBO],
    )
    opts.update(
        {
            "persistent_context": True,
            "user_data_dir": str(get_profile_dir().resolve()),
        }
    )
    _patch_persistent_context()
    browser = Camoufox(from_options=opts)
    context = browser.__enter__()
    page = context.pages[0] if context.pages else context.new_page()
    if verbose:
        print(f"[followup][dbg] 初始 context 页数: {len(context.pages)}")
    return browser, page


def _collect_frame_roots(page) -> List[Any]:
    """主 frame 深度优先 + 合并 page.frames 中尚未收录的 frame（含 OOPIF）。"""
    seen: set = set()
    ordered: List[Any] = []

    def walk(fr: Any) -> None:
        if fr in seen:
            return
        seen.add(fr)
        ordered.append(fr)
        try:
            children = list(fr.child_frames)
        except Exception:
            children = []
        for ch in children:
            walk(ch)

    try:
        walk(page.main_frame)
    except Exception:
        ordered = [page.main_frame]
        seen = {page.main_frame}
    try:
        for fr in page.frames:
            if fr not in seen:
                seen.add(fr)
                ordered.append(fr)
    except Exception:
        pass
    return ordered if ordered else [page.main_frame]


def _locator_roots_chat(page, *, verbose: bool = False, quiet: bool = False):
    """主文档 + 子 frame（递归 child_frames + page.frames）。"""
    out = _collect_frame_roots(page)
    if not quiet:
        print(f"[followup] 可搜索的 frame 数: {len(out)}（递归子 frame + 顶层合并）")
    if verbose:
        for i, r in enumerate(out):
            try:
                u = (r.url or "")[:160]
                nm = getattr(r, "name", "") or ""
            except Exception:
                u, nm = "(url?)", ""
            print(f"[followup][dbg]   root[{i}] name={nm!r} url={u}")
    return out


# 沟通页左侧列表常见容器（含历史网页版 main-list）
_LIST_SCOPES = (
    ".main-list",
    "[class*='main-list']",
    ".left-list",
    "[class*='left-list']",
    "[class*='conversation-list']",
    "[class*='session-list']",
    "[class*='chat-list']",
    "[class*='dialog-list']",
    "[class*='user-list']",
    "[class*='geek-list']",
    "[class*='middle']",
    "[class*='im-conversation']",
    "[class*='conversation-wrap']",
)

# 行级候选（在容器内或全 root 上尝试）
_ROW_SELECTORS = (
    '[class*="geek-item"]',
    '[class*="geekItem"]',
    '[class*="session-item"]',
    '[class*="sessionItem"]',
    '[class*="chat-item"]',
    '[class*="chatItem"]',
    '[class*="conversation-item"]',
    '[class*="list-item"]',
    '[class*="listItem"]',
    '[class*="dialog-item"]',
    '[class*="im-session"]',
    '[class*="item-wrap"]',
    '[class*="user-item"]',
    "li",
    "div[role='listitem']",
    "a[href*='chat']",
)


def _find_rows_scoped_in_root(root, marker: str, *, verbose: bool = False):
    """在疑似列表容器内按行 + has_text 匹配。"""
    for scope in _LIST_SCOPES:
        try:
            box = root.locator(scope).first
            if box.count() == 0:
                continue
            for rsel in _ROW_SELECTORS:
                try:
                    loc = box.locator(rsel).filter(has_text=marker)
                    cnt = loc.count()
                    if verbose:
                        print(
                            f"[followup][dbg]     scoped {scope!r} >> {rsel!r} "
                            f"has_text={marker!r} -> count={cnt}"
                        )
                    if cnt > 0:
                        return loc, f"{scope}>>{rsel}"
                except Exception as ex:
                    if verbose:
                        print(f"[followup][dbg]     scoped 异常: {ex!r}")
                    continue
        except Exception:
            continue
    return None, ""


def _find_continue_rows_in_root(root, marker: str, *, verbose: bool = False):
    """在单个 Page 或 Frame 根下查找含 marker 的列表行。"""
    loc, hint = _find_rows_scoped_in_root(root, marker, verbose=verbose)
    if loc is not None:
        if verbose and hint:
            print(f"[followup][dbg]     选用 scoped: {hint}")
        return loc
    for sel in _ROW_SELECTORS:
        try:
            loc2 = root.locator(sel).filter(has_text=marker)
            cnt = loc2.count()
            if verbose:
                print(f"[followup][dbg]     selector={sel!r} filter(has_text={marker!r}) -> count={cnt}")
            if cnt > 0:
                if verbose:
                    print(f"[followup][dbg]     选用全 root selector={sel!r}")
                return loc2
        except Exception as ex:
            if verbose:
                print(f"[followup][dbg]     selector={sel!r} 异常: {ex!r}")
            continue
    try:
        for scope in ("[class*='user-list']", ".main-list", "[class*='main-list']"):
            box = root.locator(scope).first
            if box.count() == 0:
                continue
            loc3 = box.locator(":scope > div").filter(has_text=marker)
            c3 = loc3.count()
            if verbose:
                print(
                    f"[followup][dbg]     direct child div under {scope!r} "
                    f"has_text={marker!r} -> count={c3}"
                )
            if c3 > 0:
                if verbose:
                    print(f"[followup][dbg]     选用 direct-child div under {scope!r}")
                return loc3
    except Exception as ex:
        if verbose:
            print(f"[followup][dbg]     direct-child 策略异常: {ex!r}")
    return None


def _find_continue_rows_anywhere(
    page,
    markers: List[str],
    *,
    verbose: bool = False,
    quiet_roots: bool = False,
):
    """
    在主页面与各 iframe 中查找；返回 (locator, root, matched_marker)。
    无匹配时 locator 为占位（count=0），root 为主 frame，matched_marker 为 ""。
    """
    roots = _locator_roots_chat(page, verbose=verbose, quiet=quiet_roots)
    for ri, root in enumerate(roots):
        for marker in markers:
            if not marker:
                continue
            if verbose:
                print(f"[followup][dbg] 在 root[{ri}] 上尝试 marker={marker!r} …")
            loc = _find_continue_rows_in_root(root, marker, verbose=verbose)
            if loc is not None:
                try:
                    n = loc.count()
                except Exception:
                    n = -1
                if not quiet_roots or verbose:
                    print(f"[followup] 在 root[{ri}] 命中约 {n} 条（状态文案「{marker}」）")
                if verbose:
                    try:
                        print(f"[followup][dbg] 命中 root url={(root.url or '')[:200]}")
                    except Exception:
                        pass
                return loc, root, marker
    if not quiet_roots:
        print(
            "[followup] 所有 frame 均未匹配到列表行；"
            f"已尝试文案 {markers!r}，请用 BOSS_FOLLOWUP_ROW_TEXT 增加逗号分隔别名。"
        )
        if verbose:
            print(
                "[followup][dbg] 建议：留窗后在 DevTools 看左侧列表 class 与状态字；"
                "或把 BOSS_FOLLOWUP_LIST_WAIT_SEC 调大。"
            )
    elif verbose:
        print("[followup][dbg] (quiet_roots) 本轮重扫未匹配")
        print(
            "[followup][dbg] 建议：DevTools 看左侧列表；"
            "BOSS_FOLLOWUP_ROW_TEXT 支持「继续沟通,沟通中」等多文案。"
        )
    try:
        return page.locator("#__boss_recruit_followup_no_rows__"), page.main_frame, ""
    except Exception:
        return (
            page.main_frame.locator("body").filter(has_text="__NO_MATCH__"),
            page.main_frame,
            "",
        )


def _poll_list_until_rows(page, markers: List[str], *, verbose: bool) -> Tuple[Any, Any, str, int]:
    """首轮等待后再轮询几次，缓解 SPA 列表晚于顶栏渲染。"""
    best_n = 0
    best_pack: Tuple[Any, Any, str] = (
        page.locator("#__boss_recruit_followup_no_rows__"),
        page.main_frame,
        "",
    )
    for i in range(FOLLOWUP_LIST_POLL_TRIES):
        rows, root, used = _find_continue_rows_anywhere(
            page,
            markers,
            verbose=verbose and i == 0,
            quiet_roots=i > 0,
        )
        try:
            n = rows.count()
        except Exception:
            n = 0
        if n > best_n:
            best_n = n
            best_pack = (rows, root, used)
        if n > 0:
            if i > 0:
                print(f"[followup] 列表在第 {i + 1} 次轮询时匹配到约 {n} 条")
            return rows, root, used, n
        if i < FOLLOWUP_LIST_POLL_TRIES - 1:
            try:
                page.mouse.wheel(0, 320)
            except Exception:
                pass
            time.sleep(FOLLOWUP_LIST_POLL_STEP_SEC)
    rows, root, used = best_pack
    return rows, root, used, best_n


def _preview_looks_like_multi_session(preview: str) -> bool:
    """
    宽扫描若把整块列表当成「一行」，inner_text 会拼接多个会话。
    用于拒绝过粗的 locator（避免点到错误联系人）。
    """
    p = (preview or "").strip()
    if len(p) > 380:
        return True
    times = re.findall(r"\d{1,2}:\d{2}", p)
    if len(times) >= 2:
        return True
    if p.count("seo助理") >= 2 or p.count("您好") >= 2:
        return True
    return False


def _broad_row_locator_candidates(box):
    """由细到粗尝试行级 locator（同一 list 容器）。"""
    return (
        (box.locator(":scope > div > div"), "div>div"),
        (box.locator('[class*="list-item"]'), "list-item"),
        (box.locator('[class*="ListItem"]'), "ListItem"),
        (box.locator('[class*="session-item"]'), "session-item"),
        (box.locator('[class*="SessionItem"]'), "SessionItem"),
        (box.locator('[class*="item-wrap"]'), "item-wrap"),
        (box.locator('[class*="geek-item"]'), "geek-item"),
        (box.locator("li"), "li"),
        (box.locator(":scope > div"), "div"),
    )


def _rows_pass_single_session_shape(rows, *, verbose: bool, sample: int = 5) -> bool:
    """抽样前几行：文本不能像多块拼接，且要有合理长度。"""
    try:
        cnt = rows.count()
    except Exception:
        return False
    if cnt < 1:
        return False
    for i in range(min(sample, cnt)):
        try:
            t = (rows.nth(i).inner_text(timeout=1200) or "").strip()
        except Exception:
            return False
        if len(t) < 8 or len(t) > 500:
            if verbose:
                print(f"[followup][dbg] broad 校验行{i} 长度异常 len={len(t)}")
            return False
        if _preview_looks_like_multi_session(t):
            if verbose:
                print(f"[followup][dbg] broad 校验行{i} 疑似多会话拼接，拒绝该 locator")
            return False
    return True


def _find_broad_session_rows(page, *, verbose: bool = False) -> Tuple[Any, Any]:
    """
    在 user-list / main-list 下找「一行一会话」的 locator。
    不再仅用 :scope > div（常为整块滚动区，inner_text 会拼多人导致点错行）。
    """
    for root in _locator_roots_chat(page, verbose=verbose, quiet=True):
        for scope in ("[class*='user-list']", "[class*='main-list']", ".main-list"):
            try:
                box = root.locator(scope).first
                if box.count() == 0:
                    continue
                for rows, tag in _broad_row_locator_candidates(box):
                    try:
                        cnt = rows.count()
                    except Exception:
                        continue
                    if verbose:
                        print(f"[followup][dbg] broad {scope!r} 策略={tag!r} 行数={cnt}")
                    if cnt < 1:
                        continue
                    if not _rows_pass_single_session_shape(rows, verbose=verbose):
                        continue
                    if verbose:
                        print(f"[followup][dbg] broad 选用 {scope!r} + {tag!r}（行数={cnt}）")
                    return rows, root
            except Exception:
                continue
    return None, None


def _read_chat_center_snippet(page, *, verbose: bool = False) -> str:
    """
    只读中间/右侧会话主区，避免用 body 把左侧列表整表拼进来（会导致姓名校验失效、分类误判）。
    """
    sels = (
        "[class*='im-right']",
        "[class*='chat-right']",
        "[class*='conversation-main']",
        "[class*='message-panel']",
        ".boss-chat-main",
        ".chat-main",
        "[class*='ChatMain']",
        "main",
    )
    for sel in sels:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=1500):
                continue
            t = (loc.inner_text(timeout=5000) or "").strip()
            if len(t) > 20:
                if verbose:
                    print(f"[followup][dbg] 会话主区选用选择器: {sel!r} len={len(t)}")
                return t[:1800]
        except Exception:
            continue
    try:
        return (page.locator("main").first.inner_text(timeout=3000) or "")[:1800]
    except Exception:
        return ""


def _dismiss_boss_tip_popup(page, *, verbose: bool = False) -> bool:
    """关闭「意向沟通」等引导浮层（知道了），避免挡住输入框。"""
    clicked = False
    for txt in ("知道了", "我知道了"):
        try:
            b = page.get_by_role("button", name=re.compile(re.escape(txt))).first
            if b.count() and b.is_visible(timeout=1500):
                b.click(timeout=4000)
                time.sleep(0.3)
                clicked = True
                if verbose:
                    print(f"[followup][dbg] 已点引导按钮（role=button {txt!r}）")
        except Exception:
            pass
        try:
            b2 = page.locator(
                '[class*="dialog"] button, [class*="Dialog"] button, '
                '[class*="guide"] button, [class*="popover"] button, '
                "button"
            ).filter(has_text=txt).first
            if b2.count() and b2.is_visible(timeout=1200):
                b2.click(timeout=4000)
                time.sleep(0.3)
                clicked = True
                if verbose:
                    print(f"[followup][dbg] 已点引导按钮（dialog/guide {txt!r}）")
        except Exception:
            pass
    return clicked


def _prepare_chat_composer(page, *, verbose: bool = False) -> None:
    """发送前：多次关引导 + Esc，确保输入区可点。"""
    for _ in range(3):
        _dismiss_boss_tip_popup(page, verbose=verbose)
        time.sleep(0.2)
    try:
        page.keyboard.press("Escape")
        time.sleep(0.12)
        page.keyboard.press("Escape")
        time.sleep(0.2)
    except Exception:
        pass


def _resolve_rows_for_run(
    page,
    markers: List[str],
    resume_eff: bool,
    *,
    verbose: bool,
) -> Tuple[Any, Any, str, int, str]:
    """
    返回 rows, root, used_mark, n, source。
    source 为 'broad' | 'marker'。resume_only 且未自定义 BOSS_FOLLOWUP_ROW_TEXT 时优先宽列表。
    """
    if resume_eff and not _user_set_boss_followup_row_text():
        br, broot = _find_broad_session_rows(page, verbose=verbose)
        if br is not None:
            try:
                bn = br.count()
            except Exception:
                bn = 0
            if bn > 0:
                print(
                    f"[followup] 列表来源：宽扫描（约 {bn} 行），按预览分三类；"
                    f"显式设置 BOSS_FOLLOWUP_ROW_TEXT 可改回仅按关键词匹配。"
                )
                return br, broot, "", bn, "broad"
        if verbose:
            print(
                "[followup][dbg] 宽扫描未得到「单行一会话」结构，改用关键词匹配（marker）"
            )
    rows, root, used, n = _poll_list_until_rows(page, markers, verbose=verbose)
    return rows, root, used, n, "marker"


def _row_name_preview(row) -> Tuple[str, str]:
    name, preview = "未知", ""
    try:
        for sub in ('.name', '[class*="name"]', '.geek-name'):
            n = row.locator(sub).first
            if n.count() and n.is_visible(timeout=500):
                name = (n.inner_text() or "").strip().split("\n")[0][:40]
                break
    except Exception:
        pass
    try:
        preview = (row.inner_text() or "")[:200]
    except Exception:
        pass
    return name, preview


def _row_stable_session_id(row: Any) -> str:
    """从列表行 DOM 取 Boss 常用稳定 id，避免仅用预览文案做 session_key。"""
    attrs = (
        "data-geek-id",
        "data-encryptuid",
        "data-encrypt-user-id",
        "data-id",
        "data-conversation-id",
        "data-conversationid",
        "data-uid",
        "data-iid",
    )
    for a in attrs:
        try:
            v = row.get_attribute(a)
            if v and str(v).strip():
                return f"{a}={str(v).strip()[:120]}"
        except Exception:
            continue
    try:
        got = row.evaluate(
            """(node) => {
              let el = node;
              for (let d = 0; d < 10 && el; d++) {
                if (!el.getAttribute) { el = el.parentElement; continue; }
                for (const k of ['data-geek-id','data-encryptuid','data-id',
                  'data-conversation-id','data-uid']) {
                  const v = el.getAttribute(k);
                  if (v && String(v).trim()) return k + '=' + String(v).trim().slice(0, 120);
                }
                el = el.parentElement;
              }
              return '';
            }"""
        )
        if isinstance(got, str) and got.strip():
            return got.strip()[:200]
    except Exception:
        pass
    return ""


def _try_refocus_left_chat_list(list_root: Any, *, verbose: bool = False) -> None:
    """发消息后切回左侧列表，减少下一行仍停留在同一聊天页导致误点同一人。"""
    if list_root is None:
        return
    for sel in (".user-list", "[class*='user-list']", "[class*='main-list']"):
        try:
            loc = list_root.locator(sel).first
            if not loc.count():
                continue
            if not loc.is_visible(timeout=900):
                continue
            loc.click(timeout=2500, position={"x": 24, "y": 72})
            time.sleep(0.28)
            if verbose:
                print(f"[followup][dbg] 已点击左侧列表以收回焦点（{sel!r}）")
            return
        except Exception as ex:
            if verbose:
                print(f"[followup][dbg] refocus list {sel!r}: {ex!r}")
            continue


_ROW_LIST_TIME_SUBS = (
    "[class*='time']",
    "[class*='Time']",
    ".time",
    "[class*='item-time']",
    "[class*='message-time']",
    "[class*='conversation-time']",
    "[class*='chat-time']",
    "[class*='send-time']",
    "[class*='list-time']",
    ".boss-chat-item-time",
)


def _list_time_filter_enabled() -> bool:
    return os.environ.get("BOSS_FOLLOWUP_LIST_TIME_CHECK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _skip_delivered_read_followup() -> bool:
    """列表/预览中含 [送达]/[已读] 且归类为送达/已读时，默认不再发跟进（inner_text 未必以标签开头）。"""
    return os.environ.get("BOSS_FOLLOWUP_SKIP_DELIVERED_READ", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _row_list_time_caption(row: Any) -> str:
    """读取会话行右侧时间文案（Boss 常见为 HH:mm=今日，「昨天」=非今日）。"""
    for sub in _ROW_LIST_TIME_SUBS:
        try:
            loc = row.locator(sub).last
            if not loc.count():
                continue
            t = (loc.inner_text(timeout=900) or "").strip()
            t = re.sub(r"\s+", " ", t)
            if t and len(t) <= 24:
                return t
        except Exception:
            continue
    try:
        full = (row.inner_text(timeout=1800) or "").replace("\n", " ")
    except Exception:
        return ""
    full = re.sub(r"\s+", " ", full).strip()
    for needle in ("昨天", "前天", "大前天"):
        if needle in full:
            return needle
    m = re.search(r"\d{4}-\d{1,2}-\d{1,2}", full)
    if m:
        return m.group(0)
    m = re.search(r"\d{1,2}月\d{1,2}日", full)
    if m:
        return m.group(0)
    m = re.search(r"(?<![\d])(\d{1,2}-\d{1,2})(?![\d])", full)
    if m:
        return m.group(1)
    times = re.findall(r"(?<![\d])(\d{1,2}:\d{2})(?![\d])", full)
    if times:
        return times[-1]
    if "刚刚" in full:
        return "刚刚"
    m = re.search(r"\d{1,3}\s*分钟前", full)
    if m:
        return m.group(0).replace(" ", "")
    m = re.search(r"\d{1,2}\s*小时前", full)
    if m:
        return m.group(0).replace(" ", "")
    return ""


def _should_process_row_by_list_time(caption: str) -> Tuple[bool, str]:
    """
    是否因「列表时间」而应处理本会话行。
    True：今日常见展示；False：昨日或日历日期等。
    """
    c = (caption or "").strip()
    if not c:
        return True, "未读到时间（仍处理，避免漏抓）"
    if "昨天" in c or "前天" in c or "大前天" in c:
        return False, "列表含昨天/前天"
    if "今天" in c or "今日" in c:
        return True, "显式今日"
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}", c):
        return False, "完整日期"
    if re.search(r"\d{1,2}月\d{1,2}日", c):
        return False, "月日格式"
    if re.match(r"^\d{1,2}-\d{1,2}$", c):
        return False, "月-日（非今日）"
    if c == "刚刚" or "分钟前" in c or "小时前" in c:
        return True, "相对时间（当日）"
    if re.match(r"^\d{1,2}:\d{2}$", c):
        return True, "HH:mm（Boss 列表通常为今日）"
    if len(c) <= 24:
        return True, f"未识别形态 {c!r}（仍处理）"
    return True, "仍处理"


def _click_resume_consent_agree(page, *, dry_run: bool, verbose: bool) -> bool:
    """对方请求发附件简历时，尝试点击「同意」类按钮。"""
    if dry_run:
        print("    [dry-run] 将尝试点击「同意/接收」以同意对方发送简历")
        return True
    labels = ("同意", "接收简历", "接受", "允许", "接收")
    contexts: List[Any] = [page]
    seen_ids = {id(page)}
    for fr in _collect_frame_roots(page):
        if id(fr) in seen_ids:
            continue
        seen_ids.add(id(fr))
        contexts.append(fr)
    for ctx in contexts:
        for lb in labels:
            try:
                btn = ctx.get_by_role("button", name=re.compile(re.escape(lb))).first
                if btn.count() and btn.is_visible(timeout=1500):
                    btn.click(timeout=5000)
                    time.sleep(0.9)
                    print(f"    [OK] 已点击「{lb}」（同意简历）")
                    return True
            except Exception:
                pass
            try:
                b2 = ctx.locator("button, a").filter(has_text=lb).first
                if b2.count() and b2.is_visible(timeout=1000):
                    b2.click(timeout=5000)
                    time.sleep(0.9)
                    print(f"    [OK] 已点击控件「{lb}」（同意简历）")
                    return True
            except Exception as ex:
                if verbose:
                    print(f"    [followup][dbg] 同意按钮 {lb!r}: {ex!r}")
                continue
    if verbose:
        print("    [followup][dbg] 未找到可见的「同意」类按钮（可能已处理或需人工）")
    return False


def _fill_boss_chat_editor_js(frame_or_page: Any, text: str, *, verbose: bool) -> bool:
    """web/chat 等页：输入区常为 #boss-chat-editor-input（contenteditable），用 DOM 写入并触发 input。"""
    js = """
    (t) => {
      const el =
        document.getElementById("boss-chat-editor-input") ||
        document.querySelector(".boss-chat-editor-input[contenteditable=\\"true\\"]");
      if (!el || !el.isContentEditable) return false;
      el.focus();
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
      el.click();
      el.textContent = t;
      try {
        el.dispatchEvent(
          new InputEvent("input", { bubbles: true, inputType: "insertFromPaste", data: t })
        );
      } catch (e) {
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return true;
    }
    """
    try:
        ok = bool(frame_or_page.evaluate(js, text))
        if ok:
            time.sleep(0.35)
        return ok
    except Exception as ex:
        if verbose:
            print(f"    [followup][dbg] _fill_boss_chat_editor_js: {ex!r}")
        return False


def _click_send_by_label_js(frame_or_page: Any, *, verbose: bool) -> bool:
    """发送键不一定是 <button>；在可点元素中按可见文案「发送」匹配。"""
    js = r"""
    () => {
      const scope =
        document.querySelector(".boss-chat-main") ||
        document.querySelector("[class*='boss-chat-footer']") ||
        document.querySelector("[class*='boss-chat']") ||
        document.body;
      const cand = Array.from(
        scope.querySelectorAll(
          'button, a, div[role="button"], span[role="button"], i[role="button"], [class*="send"]'
        )
      );
      for (const el of cand) {
        const raw = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
        if (!raw || raw.length > 12) continue;
        if (raw === "发送" || raw === "发 送" || /^发送/.test(raw)) {
          const r = el.getBoundingClientRect();
          if (r.width < 2 || r.height < 2) continue;
          const st = window.getComputedStyle(el);
          if (st.visibility === "hidden" || st.display === "none") continue;
          if (st.pointerEvents === "none") continue;
          el.click();
          return true;
        }
      }
      return false;
    }
    """
    try:
        ok = bool(frame_or_page.evaluate(js))
        return ok
    except Exception as ex:
        if verbose:
            print(f"    [followup][dbg] _click_send_by_label_js: {ex!r}")
        return False


def _focus_bottom_composer_js(frame_or_page, *, verbose: bool) -> bool:
    """
    不依赖 class 名：在文档里找靠近视口底部的可编辑节点并 focus+click。
    用于 Boss Web 沟通页输入区在 Shadow/复杂结构里、Playwright 选择器扫不到的情况。
    """
    js = """
    () => {
      const pinned = document.getElementById("boss-chat-editor-input");
      if (pinned && pinned.isContentEditable && pinned.offsetParent) {
        pinned.focus();
        pinned.click();
        return true;
      }
      const all = Array.from(document.querySelectorAll(
        '[contenteditable="true"], textarea:not([readonly]):not([disabled])'
      ));
      let best = null;
      let bestArea = -1;
      for (const el of all) {
        if (!el.offsetParent) continue;
        const st = window.getComputedStyle(el);
        if (st.visibility === "hidden" || st.display === "none") continue;
        const r = el.getBoundingClientRect();
        if (r.height < 16 || r.width < 100) continue;
        const gapBottom = window.innerHeight - r.bottom;
        if (gapBottom > 220) continue;
        const area = r.width * Math.min(r.height, 400);
        if (area > bestArea) {
          bestArea = area;
          best = el;
        }
      }
      if (best) {
        best.focus();
        best.click();
        return true;
      }
      return false;
    }
    """
    try:
        ok = frame_or_page.evaluate(js)
        if ok and verbose:
            try:
                u = getattr(frame_or_page, "url", None) or ""
            except Exception:
                u = ""
            print(f"[followup][dbg] JS 已聚焦底部输入区（document 片段: {(u or '')[:100]}）")
        return bool(ok)
    except Exception as ex:
        if verbose:
            print(f"[followup][dbg] JS 聚焦底部输入区失败: {ex!r}")
        return False


_DOM_HINTS_JS = r"""
() => {
  function rect(el) {
    const r = el.getBoundingClientRect();
    return {
      top: Math.round(r.top),
      left: Math.round(r.left),
      width: Math.round(r.width),
      height: Math.round(r.height),
      bottom: Math.round(r.bottom)
    };
  }
  function brief(el, extra) {
    const o = {
      tag: el.tagName,
      id: el.id || "",
      cls: (typeof el.className === "string" ? el.className : (el.className && el.baseVal) || "")
        .slice(0, 260),
      ph: (el.getAttribute && el.getAttribute("placeholder")) || "",
      role: (el.getAttribute && el.getAttribute("role")) || "",
      ce: (el.getAttribute && el.getAttribute("contenteditable")) || "",
      name: (el.getAttribute && el.getAttribute("name")) || "",
      type: (el.getAttribute && el.getAttribute("type")) || "",
      r: rect(el)
    };
    if (extra) Object.assign(o, extra);
    return o;
  }
  const hints = {
    textareas: [],
    contenteditables: [],
    labeledButtons: [],
    listRoots: []
  };
  document.querySelectorAll("textarea").forEach((el) => {
    hints.textareas.push(brief(el));
  });
  document.querySelectorAll("[contenteditable]").forEach((el) => {
    const v = (el.getAttribute && el.getAttribute("contenteditable")) || "";
    if (v === "true" || v === "") hints.contenteditables.push(brief(el));
  });
  document.querySelectorAll("button, a, [role='button']").forEach((el) => {
    const t = (el.innerText || "").replace(/\s+/g, " ").trim();
    if (/发送|知道了|同意|求简历/.test(t)) {
      hints.labeledButtons.push(brief(el, { label: t.slice(0, 40) }));
    }
  });
  document.querySelectorAll("[class*='user-list'],[class*='main-list'],[class*='chat-list']").forEach((el) => {
    hints.listRoots.push(
      brief(el, { childCount: el.children ? el.children.length : -1 })
    );
  });
  return hints;
}
"""


def _collect_dom_hints_from_frame(fr: Any) -> Dict[str, Any]:
    try:
        u = (fr.url or "")[:400]
    except Exception:
        u = ""
    try:
        data = fr.evaluate(_DOM_HINTS_JS)
        return {"frame_url": u, "hints": data}
    except Exception as e:
        return {"frame_url": u, "error": repr(e)}


def export_followup_dom_hints(page, *, verbose: bool = False) -> Path:
    """把各 frame 内 textarea / contenteditable / 关键按钮 / 列表容器的 class 等写入 JSON。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"followup_dom_hints_{ts}.json"
    payload: Dict[str, Any] = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "page_url": "",
        "frames": [],
    }
    try:
        payload["page_url"] = (page.url or "")[:500]
    except Exception:
        pass
    seen = set()
    for fr in _collect_frame_roots(page):
        try:
            fid = id(fr)
        except Exception:
            fid = 0
        if fid in seen:
            continue
        seen.add(fid)
        payload["frames"].append(_collect_dom_hints_from_frame(fr))
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[followup] DOM 线索已写入: {out}")
    if verbose:
        for block in payload["frames"][:6]:
            print(f"[followup][dbg] frame: {(block.get('frame_url') or '')[:120]}")
            if block.get("error"):
                print(f"    err: {block['error']}")
            else:
                h = block.get("hints") or {}
                print(f"    textareas={len(h.get('textareas') or [])} "
                      f"contenteditables={len(h.get('contenteditables') or [])} "
                      f"buttons={len(h.get('labeledButtons') or [])} "
                      f"listRoots={len(h.get('listRoots') or [])}")
    return out


def run_followup_dump_dom(chat_url: str, *, keep_open: bool, verbose: bool) -> None:
    """打开沟通页 → 等用户在页面中点开会话 → 终端 Enter 后再导出 DOM，避免未选中联系人时 JSON 为空。"""
    v = verbose or _env_verbose()
    browser = None
    try:
        browser, page = _launch_browser(verbose=v)
        page.goto(chat_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        time.sleep(2.0)
        if not probe_logged_in(page, label="chat-dump"):
            print("[login] 未登录，请先 py scripts/login.py 后重试 --dump-dom")
            return
        time.sleep(float(os.environ.get("BOSS_FOLLOWUP_LIST_WAIT_SEC", "4")))
        _dismiss_boss_tip_popup(page, verbose=v)
        time.sleep(0.4)
        skip_prep = os.environ.get("BOSS_DUMP_DOM_SKIP_PREP_WAIT", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not skip_prep:
            print(
                "\n[followup][--dump-dom] 请在浏览器中点击左侧要调试的会话，"
                "待右侧出现聊天区/输入框后，回到本窗口按 Enter 开始写入 DOM…"
            )
            try:
                input()
            except EOFError:
                print("[followup][WARN] 无 stdin，跳过等待，立即导出（可设 BOSS_DUMP_DOM_SKIP_PREP_WAIT=1 显式跳过）")
        export_followup_dom_hints(page, verbose=v)
        if keep_open:
            try:
                input("\n[followup] DOM 已写入。按 Enter 关闭浏览器…")
            except EOFError:
                time.sleep(30)
    finally:
        if browser is not None:
            browser.__exit__(None, None, None)


def _send_in_chat(page, text: str, dry_run: bool, *, verbose: bool = False) -> bool:
    if dry_run:
        print(f"    [dry-run] 将发送: {text[:200]}...")
        if verbose:
            print(f"    [followup][dbg] dry-run 全文长度={len(text)} 字符")
        return True

    _prepare_chat_composer(page, verbose=verbose)

    selectors_ta = (
        "textarea",
        "textarea[placeholder*='输入']",
        "textarea[placeholder*='请输入']",
        "textarea[placeholder*='消息']",
        "textarea[placeholder*='和牛人']",
        "textarea.duty-input",
        "textarea[class*='input']",
        "textarea[class*='editor']",
        ".boss-chat-editor textarea",
        "[class*='chat-editor'] textarea",
        "[class*='im-input'] textarea",
        "[class*='im-text'] textarea",
        "[class*='send-box'] textarea",
        "[class*='duty-send'] textarea",
        ".input-area textarea",
        "[class*='footer'] textarea",
        "[class*='editor'] textarea",
        "[class*='message-input'] textarea",
    )
    contenteditable_sels = (
        "#boss-chat-editor-input",
        "div#boss-chat-editor-input",
        ".boss-chat-editor-input[contenteditable='true']",
        ".boss-chat-editor [contenteditable='true']",
        "[class*='boss-chat-editor'] [contenteditable='true']",
        "[class*='chat-editor'] [contenteditable='true']",
        "[class*='im-input'] [contenteditable='true']",
        "[class*='im-text'] [contenteditable='true']",
        "[class*='send-area'] [contenteditable='true']",
        "[class*='duty-send'] [contenteditable='true']",
        "[contenteditable='true'][data-placeholder]",
        "div[role='textbox']",
        "[class*='im-editor'] [contenteditable='true']",
        "[class*='editor-area'] [contenteditable='true']",
        "[class*='ql-editor']",
        "[class*='ProseMirror']",
    )
    scoped = (
        page.locator("[class*='chat-footer']"),
        page.locator("[class*='im-footer']"),
        page.locator("[class*='im-chat']"),
        page.locator("[class*='duty-send']"),
        page.locator("[class*='send-box']"),
        page.locator("[class*='chat-bottom']"),
        page.locator("[class*='bottom-editor']"),
        page.locator(".boss-chat-editor"),
        page.locator("[class*='boss-chat-footer']"),
        page.locator("[class*='boss-chat-editor-wrap']"),
        page.locator("[class*='input-wrap']"),
        page.locator("[class*='message-editor']"),
        page.locator("footer"),
    )

    contexts: List[Any] = [page]
    seen_ids = {id(page)}
    for fr in _collect_frame_roots(page):
        if id(fr) in seen_ids:
            continue
        seen_ids.add(id(fr))
        contexts.append(fr)

    def try_fill(loc, sel_tag: str) -> bool:
        try:
            if loc.count() == 0:
                return False
            loc.scroll_into_view_if_needed(timeout=5000)
            time.sleep(0.12)
            try:
                loc.click(timeout=4000)
            except Exception:
                try:
                    loc.click(timeout=4000, force=True)
                except Exception:
                    return False
            time.sleep(0.2)
            try:
                loc.fill(text, timeout=8000)
            except Exception:
                try:
                    loc.press_sequentially(text, delay=15, timeout=120000)
                except Exception as ex2:
                    if verbose:
                        print(f"    [followup][dbg] fill/press_sequentially 失败: {ex2!r}")
                    return False
            time.sleep(0.35)
            print(f"    [followup] 已写入输入区: {sel_tag!r}")
            return True
        except Exception as ex:
            if verbose:
                print(f"    [followup][dbg] try_fill 异常 {sel_tag!r}: {ex!r}")
            return False

    used_tag = ""
    priority_ce = (
        "#boss-chat-editor-input",
        "div#boss-chat-editor-input",
        ".boss-chat-editor-input[contenteditable='true']",
    )
    for ctx in contexts:
        for sel in priority_ce:
            tag = f"{type(ctx).__name__}>>{sel}"
            if try_fill(ctx.locator(sel).first, tag):
                used_tag = tag
                break
        if used_tag:
            break
    if not used_tag:
        for ctx in contexts:
            if _fill_boss_chat_editor_js(ctx, text, verbose=verbose):
                used_tag = f"js>>boss-chat-editor-input::{type(ctx).__name__}"
                print(
                    f"    [followup] 已通过 JS 写入沟通输入区（{type(ctx).__name__}）"
                )
                break

    if not used_tag:
        for root in scoped:
            try:
                if root.count() == 0:
                    continue
            except Exception:
                continue
            for sel in selectors_ta:
                tag = f"scoped>>{sel}"
                if try_fill(root.locator(sel).first, tag):
                    used_tag = tag
                    break
            if used_tag:
                break
            for sel in contenteditable_sels:
                tag = f"scoped>>{sel}"
                if try_fill(root.locator(sel).first, tag):
                    used_tag = tag
                    break
            if used_tag:
                break

    if not used_tag:
        for ctx in contexts:
            for sel in selectors_ta:
                tag = f"{type(ctx).__name__}>>{sel}"
                if try_fill(ctx.locator(sel).first, tag):
                    used_tag = tag
                    break
            if used_tag:
                break
            for sel in contenteditable_sels:
                tag = f"{type(ctx).__name__}>>{sel}"
                if try_fill(ctx.locator(sel).first, tag):
                    used_tag = tag
                    break
            if used_tag:
                break

    if not used_tag:
        for fr in contexts:
            if _focus_bottom_composer_js(fr, verbose=verbose):
                try:
                    page.keyboard.type(text, delay=12)
                    time.sleep(0.35)
                    used_tag = "js-bottom-focus+keyboard.type"
                    print(f"    [followup] 已通过 JS 聚焦底部输入区并 keyboard.type 写入（{type(fr).__name__}）")
                    break
                except Exception as ex:
                    if verbose:
                        print(f"    [followup][dbg] keyboard.type 失败: {ex!r}")

    if not used_tag:
        print(
            "    [WARN] 仍未找到输入区；若页面有「知道了」请先手动点掉，或升级 camoufox/playwright 后重试。"
        )
        return False

    for sbtn in (
        '[class*="boss-chat"] button:has-text("发送")',
        ".boss-main-send-msg",
        '[class*="send-msg"]',
        '[class*="boss-main-send"]',
        'button:has-text("发送")',
        "button.btn-send",
        ".btn-send",
        ".send-btn",
        '[class*="send"]',
        "button[type='submit']",
    ):
        for ctx in contexts:
            try:
                b = ctx.locator(sbtn).first
                bc = b.count()
                if verbose:
                    print(f"    [followup][dbg] 发送按钮 {type(ctx).__name__} {sbtn!r} count={bc}")
                if bc and b.is_visible(timeout=1800):
                    b.click(timeout=5000)
                    time.sleep(1.0)
                    print(f"    [OK] 已点击发送（{sbtn!r}）")
                    return True
            except Exception as ex:
                if verbose:
                    print(f"    [followup][dbg] 发送按钮 {sbtn!r} 失败: {ex!r}")
                continue
    for ctx in contexts:
        if _click_send_by_label_js(ctx, verbose=verbose):
            time.sleep(1.0)
            print("    [OK] 已点击发送（JS 文案匹配）")
            return True
    try:
        sb = page.get_by_role("button", name=re.compile(r"\s*发送\s*")).first
        if sb.count() and sb.is_visible(timeout=2000):
            sb.click(timeout=5000)
            time.sleep(1.0)
            print("    [OK] 已点击发送（get_by_role 发送）")
            return True
    except Exception as ex:
        if verbose:
            print(f"    [followup][dbg] get_by_role 发送: {ex!r}")
    try:
        if verbose:
            print("    [followup][dbg] 尝试 keyboard Enter 发送")
        page.keyboard.press("Enter")
        time.sleep(1.0)
        print("    [OK] 已尝试 Enter 发送")
        return True
    except Exception as ex:
        if verbose:
            print(f"    [followup][dbg] Enter 发送失败: {ex!r}")
        return False


def run_followup(
    max_items: int,
    dry_run: bool,
    chat_url: str,
    *,
    keep_open: bool = False,
    verbose: bool = False,
    resume_only: bool = False,
) -> int:
    cfg = load_followup_config()
    state = load_state()
    sessions: Dict[str, Any] = state.setdefault("sessions", {})
    today = _today_key()
    resume_eff = resume_only or bool(cfg.get("resume_only"))

    v = verbose or _env_verbose()
    markers = _effective_row_markers(resume_eff)
    tab_label = _resolve_list_tab_label()

    print(f"[followup] 沟通页: {chat_url}")
    print(f"[followup] 本轮最多处理 {max_items} 个列表会话（dry_run={dry_run}）")
    if resume_eff and not _user_set_boss_followup_row_text():
        print(
            "[followup] 未设置 BOSS_FOLLOWUP_ROW_TEXT：--resume-only 下已自动加入 "
            "网页版常见前缀 [送达]、[已读]、对方愿发送、我们正在招聘（仍可用环境变量覆盖）"
        )
    print(
        f"[followup] 列表状态文案（多选任一命中）: {markers!r} | "
        f"分隔符：逗号 / | / ；"
    )
    if _list_time_filter_enabled():
        print(
            "[followup] 列表时间过滤：已开启（跳过「昨天」及月日等；"
            "仅处理今日类如 HH:mm / 刚刚）。"
            "关闭：BOSS_FOLLOWUP_LIST_TIME_CHECK=0"
        )
    if _skip_delivered_read_followup():
        print(
            "[followup] 送达/已读过滤：已开启（归类为「送达/已读」即预览含 [送达]/[已读] 则不发跟进）。"
            "关闭：BOSS_FOLLOWUP_SKIP_DELIVERED_READ=0"
        )
    print(
        f"[followup] 列表首等待 {FOLLOWUP_LIST_WAIT_SEC:.0f}s，"
        f"轮询最多 {FOLLOWUP_LIST_POLL_TRIES} 次 / 间隔 {FOLLOWUP_LIST_POLL_STEP_SEC}s"
    )
    if resume_eff:
        print("[followup] 模式：仅索要简历（--resume-only 或 config followup.resume_only）")
    print(
        f"[followup] 点开会话后再等 {AFTER_OPEN_CHAT_SEC:.1f}s 读正文（BOSS_FOLLOWUP_AFTER_OPEN_CHAT_SEC）"
    )
    if v:
        print(
            f"[followup][dbg] CONFIG_PATH={CONFIG_PATH} exists={CONFIG_PATH.exists()} | "
            f"STATE_FILE={STATE_FILE} sessions={len(sessions)}"
        )
        loc_preview = (cfg.get("company_location") or "")[:80]
        print(
            f"[followup][dbg] followup.company_location 预览: {loc_preview!r} | "
            f"pause_after_send_sec={cfg.get('pause_after_send_sec')}"
        )

    browser = None
    sent = 0
    try:
        browser, page = _launch_browser(verbose=v)
        if v:
            print(f"[followup][dbg] page.goto wait_until=domcontentloaded …")
        page.goto(chat_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("load", timeout=15000)
            if v:
                print("[followup][dbg] wait_for_load_state(load) 已满足或超时结束")
        except Exception as ex:
            if v:
                print(f"[followup][dbg] wait_for_load_state: {ex!r}")
            pass
        time.sleep(2.0)
        try:
            u = page.url or ""
            print(f"[followup] 当前页面 URL（跳转后）: {u[:200]}{'…' if len(u) > 200 else ''}")
        except Exception:
            pass

        if v:
            print("[followup][dbg] 调用 probe_logged_in(label=chat) …")
        if not probe_logged_in(page, label="chat"):
            print("[login] 未登录或探测失败，请先在同一 profile 下执行 py scripts/login.py 扫码")
            try:
                print(f"[followup] 失败时 URL: {(page.url or '')[:220]}")
            except Exception:
                pass
            return 0
        if v:
            try:
                un = page.locator(".user-name").first.inner_text(timeout=1000)
            except Exception:
                un = "(读不到)"
            print(f"[followup][dbg] 顶栏 .user-name 文本预览: {(un or '')[:40]!r}")

        if tab_label:
            print(
                f"[followup] 列表分区：将点击「{tab_label}」"
                f"（BOSS_FOLLOWUP_LIST_TAB；设为 \"\" 可只留在「全部」）"
            )
            _click_list_filter_tab(page, tab_label, verbose=v)
        elif v and resume_eff and "BOSS_FOLLOWUP_LIST_TAB" in os.environ:
            print("[followup][dbg] BOSS_FOLLOWUP_LIST_TAB 为空，不切换分区")

        print(
            f"[followup] 等待列表渲染 {FOLLOWUP_LIST_WAIT_SEC:.0f}s（可设 BOSS_FOLLOWUP_LIST_WAIT_SEC）"
        )
        time.sleep(FOLLOWUP_LIST_WAIT_SEC)
        try:
            if v:
                print("[followup][dbg] mouse.wheel(0, 400) 触发列表区域滚动")
            page.mouse.wheel(0, 400)
            time.sleep(0.6)
        except Exception as ex:
            if v:
                print(f"[followup][dbg] wheel 异常: {ex!r}")
            pass

        _dismiss_boss_tip_popup(page, verbose=v)

        rows, list_root, used_mark, n, list_source = _resolve_rows_for_run(
            page, markers, resume_eff, verbose=v
        )
        hit_msg = f"（命中文案「{used_mark}」）" if used_mark else ""
        src_msg = f" [来源={list_source}]" if list_source else ""
        print(f"[followup] 匹配到约 {n} 条列表项{hit_msg}{src_msg}")
        if v:
            try:
                lr = (list_root.url or "")[:180]
            except Exception:
                lr = "?"
            print(f"[followup][dbg] 当前用于列表匹配的 list_root.url 预览: {lr}")
        if n == 0:
            print(
                "[followup] 若为 0：① 左侧状态字是否与 markers 一致；"
                "② 用 BOSS_FOLLOWUP_ROW_TEXT 增加别名，如「继续沟通,新招呼」；"
                "③ 调大 BOSS_FOLLOWUP_LIST_WAIT_SEC / BOSS_FOLLOWUP_LIST_POLL_TRIES；"
                "④ -v / --dry-run 会默认留窗自查。"
            )

        idx = 0
        handled = 0
        agree_clicks = 0
        processed_session_keys: set[str] = set()
        while handled < max_items:
            if list_source == "broad":
                rows, list_root = _find_broad_session_rows(page, verbose=False)
                if rows is None:
                    if v:
                        print("[followup][dbg] 宽列表不可用，结束")
                    break
                n = rows.count()
            else:
                rows, _, _ = _find_continue_rows_anywhere(
                    page, markers, verbose=v, quiet_roots=True
                )
                n = rows.count()
            if v:
                print(
                    f"[followup][dbg] --- idx={idx} n={n} handled={handled}/{max_items} "
                    f"source={list_source} ---"
                )
            if idx >= n:
                if v:
                    print(f"[followup][dbg] idx >= n，结束循环")
                break
            row = rows.nth(idx)
            idx += 1
            try:
                if not row.is_visible(timeout=1500):
                    if v:
                        print(f"[followup][dbg] 第 {idx} 行不可见，skip")
                    continue
            except Exception as ex:
                if v:
                    print(f"[followup][dbg] is_visible 异常 skip: {ex!r}")
                continue

            name, preview = _row_name_preview(row)
            if _list_time_filter_enabled():
                time_cap = _row_list_time_caption(row)
                ok_time, time_reason = _should_process_row_by_list_time(time_cap)
                if not ok_time:
                    print(
                        f"    [skip] {name} 列表时间「{time_cap or '?'}」→ {time_reason}"
                    )
                    continue
                if v and time_cap:
                    print(
                        f"    [followup][dbg] 列表时间「{time_cap}」→ {time_reason}"
                    )
            kind = classify_row_preview(preview)
            print(f"    [分类] {name} → {_row_kind_label_cn(kind)}")
            if kind == ROW_KIND_UNKNOWN:
                if v:
                    print(f"    [skip] {name} 预览未归入三类，跳过")
                continue

            if _skip_delivered_read_followup() and kind == ROW_KIND_OUTBOUND_WAITING:
                print(
                    f"    [skip] {name} 列表归类为送达/已读（预览中含 [送达]/[已读]），不发跟进"
                )
                continue

            row_uid = _row_stable_session_id(row)
            key = session_key(name, preview, stable_row_id=row_uid)
            if key in processed_session_keys:
                print(
                    f"    [skip] {name} 本轮已处理过该会话（去重），避免停留在同一聊天页时重复发送"
                )
                continue
            if v and row_uid:
                print(f"[followup][dbg] 行稳定 id: {row_uid[:120]!r}")

            rec = sessions.get(key) or {}
            last_day = rec.get("last_day")
            rounds = int(rec.get("rounds", 0))
            max_per = int(os.environ.get("BOSS_FOLLOWUP_MAX_PER_DAY", "2"))
            if v:
                print(
                    f"[followup][dbg] session key={key!r} last_day={last_day!r} "
                    f"rounds={rounds} max_per_day={max_per}"
                )
            if last_day == today and rounds >= max_per:
                print(f"    [skip] {name} 今日跟进已达上限")
                continue

            if resume_eff and _looks_like_resume_received(preview):
                print(f"    [skip] {name} 列表预览已像「已发简历」，不重复索要")
                continue

            if v:
                pv = (preview or "").replace("\n", " ")[:160]
                print(f"[followup][dbg] 将点击行 name={name!r} 行文本预览: {pv!r}")
            try:
                try:
                    row.scroll_into_view_if_needed(timeout=5000)
                    time.sleep(0.2)
                except Exception:
                    pass
                row.click(timeout=5000)
                if v:
                    print("[followup][dbg] row.click 完成")
            except Exception as e:
                print(f"    [WARN] 点击会话失败 {name}: {e}")
                continue

            time.sleep(AFTER_OPEN_CHAT_SEC)
            _dismiss_boss_tip_popup(page, verbose=v)
            time.sleep(0.35)
            chat_snippet = _read_chat_center_snippet(page, verbose=v)
            if not (chat_snippet or "").strip():
                chat_snippet = preview
                if v:
                    print("[followup][dbg] 主区文本为空，暂用列表 preview 作上下文（不做姓名交叉校验）")
            if v:
                cs = chat_snippet.replace("\n", " ")[:200]
                print(f"[followup][dbg] chat_snippet（主区）预览: {cs!r}…")

            if name and name != "未知" and len((chat_snippet or "").strip()) >= 30:
                head = (chat_snippet or "")[:900]
                if name not in head:
                    time.sleep(0.85)
                    chat_snippet = _read_chat_center_snippet(page, verbose=v)
                    head = (chat_snippet or "")[:900]
                if name not in head:
                    print(
                        f"    [followup][WARN] 会话主区未见列表姓名「{name}」，"
                        "疑未点到对应行，跳过本条。"
                    )
                    try:
                        page.keyboard.press("Escape")
                        time.sleep(0.35)
                    except Exception:
                        pass
                    continue

            blob = ((preview or "") + "\n" + (chat_snippet or ""))[:600]
            effective_kind = classify_row_preview(blob)
            if effective_kind == ROW_KIND_UNKNOWN:
                effective_kind = kind
            if effective_kind != kind and v:
                print(
                    f"    [分类] 结合聊天区校正为 → {_row_kind_label_cn(effective_kind)}"
                )

            if effective_kind == ROW_KIND_RESUME_CONSENT:
                ok_agree = _click_resume_consent_agree(page, dry_run=dry_run, verbose=v)
                if ok_agree:
                    agree_clicks += 1
                    handled += 1
                    processed_session_keys.add(key)
                    if not dry_run:
                        rec["last_day"] = today
                        rec["rounds"] = rounds + 1
                        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
                        rec["last_action"] = "resume_consent_agree"
                        sessions[key] = rec
                        save_state(state)
                        if v:
                            print(f"[followup][dbg] 已写入 state: key={key!r}")
                    pause = float(cfg.get("pause_after_send_sec") or 6)
                    print(f"    [followup] 等待 {pause:.0f}s 防风控…")
                    time.sleep(pause)
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.45)
                    _try_refocus_left_chat_list(list_root, verbose=v)
                except Exception:
                    pass
                continue

            if not resume_eff and effective_kind not in (
                ROW_KIND_OUTBOUND_WAITING,
                ROW_KIND_CANDIDATE_INITIATED,
            ):
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                except Exception:
                    pass
                continue

            if resume_eff and _looks_like_resume_received(chat_snippet):
                print(f"    [skip] {name} 聊天区已像「已发简历」，不重复索要")
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                except Exception:
                    pass
                continue

            if _skip_delivered_read_followup() and effective_kind == ROW_KIND_OUTBOUND_WAITING:
                print(
                    f"    [skip] {name} 有效类型仍为送达/已读，不发跟进（已打开会话则关闭）"
                )
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.45)
                    _try_refocus_left_chat_list(list_root, verbose=v)
                except Exception:
                    pass
                continue

            msg = build_message(cfg, rounds, name, chat_snippet, resume_only=resume_eff)
            print(
                f"\n  [会话] {name} | 类型={_row_kind_label_cn(effective_kind)} | "
                f"跟进轮次={rounds} -> 文案预览: {msg[:120]}..."
            )

            ok = _send_in_chat(page, msg, dry_run=dry_run, verbose=v)
            if ok:
                handled += 1
                sent += 1
                processed_session_keys.add(key)
                if not dry_run:
                    rec["last_day"] = today
                    rec["rounds"] = rounds + 1
                    rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    rec["last_action"] = "resume_ask"
                    sessions[key] = rec
                    save_state(state)
                    if v:
                        print(f"[followup][dbg] 已写入 state: key={key!r}")
                pause = float(cfg.get("pause_after_send_sec") or 6)
                print(f"    [followup] 等待 {pause:.0f}s 防风控…")
                time.sleep(pause)
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
                _try_refocus_left_chat_list(list_root, verbose=v)
                if v:
                    print("[followup][dbg] 已按 Escape 尝试关闭浮层并切回左侧列表")
            except Exception:
                pass

        print(
            f"\n[followup] 本轮完成：发送话术 {sent} 条；同意简历 {agree_clicks} 次（dry_run={dry_run}）"
        )

        if keep_open:
            try:
                input(
                    "\n[followup] 按 Enter 关闭浏览器（当前窗口可用来检查列表 DOM、文案、iframe）…"
                )
            except EOFError:
                time.sleep(45)
                print("[followup] 无 stdin，等待 45s 后关闭…")

        return sent
    finally:
        if browser is not None:
            browser.__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(
        description="招聘方：对沟通列表中含「继续沟通」的会话做智能短跟进",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=int(os.environ.get("BOSS_FOLLOWUP_MAX", "5")),
        metavar="N",
        help="本轮最多跟进多少个会话（默认 5）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要发送的文案，不实际输入/发送",
    )
    parser.add_argument(
        "--chat-url",
        default=DEFAULT_CHAT_URL,
        help="沟通页 URL（默认招聘端 boss/chat）",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="结束前不立即关浏览器，按 Enter 后再关（便于对照页面改选择器/文案）",
    )
    parser.add_argument(
        "--no-keep-open",
        action="store_true",
        help="取消「dry-run / -v 时默认留窗」；也支持环境变量 BOSS_FOLLOWUP_NO_KEEP_OPEN=1",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="详细调试输出（也可用环境变量 BOSS_FOLLOWUP_VERBOSE=1）",
    )
    parser.add_argument(
        "--dump-dom",
        action="store_true",
        help="打开沟通页 → 你在页面中点开会话 → 终端按 Enter 后写入 reports/followup_dom_hints_*.json（无需 F12）",
    )
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="每条只发索要简历话术（也可用 config.json followup.resume_only 或 BOSS_FOLLOWUP_RESUME_ONLY=1）",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        import io

        if sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )

    v_flag = args.verbose or _env_verbose()
    explicit_keep = args.keep_open or _env_keep_open()
    auto_keep = (
        args.dry_run or v_flag or args.dump_dom
    ) and not args.no_keep_open and not _env_no_keep_open()
    keep_open_effective = explicit_keep or auto_keep
    if auto_keep and not explicit_keep:
        print(
            "[followup] 因 --dry-run、-v 或 --dump-dom：默认留窗，按 Enter 后再关浏览器。"
            "若需跑完立即关，请加 --no-keep-open 或设 BOSS_FOLLOWUP_NO_KEEP_OPEN=1。"
        )

    if args.dump_dom:
        run_followup_dump_dom(
            args.chat_url,
            keep_open=keep_open_effective,
            verbose=v_flag,
        )
    else:
        run_followup(
            max_items=max(0, args.max),
            dry_run=args.dry_run,
            chat_url=args.chat_url,
            keep_open=keep_open_effective,
            verbose=args.verbose,
            resume_only=args.resume_only,
        )


if __name__ == "__main__":
    main()
