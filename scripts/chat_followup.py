#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招聘方：沟通列表中「继续沟通」会话的智能跟进（短对话：地址通勤、经历追问、索要简历等）。

- 与 greet.py 一致：Camoufox 持久化 profile（recruit_profile）、固定 seed、排除 UBO。
- 默认打开招聘端沟通页；若贵司实际入口不同，设环境变量 BOSS_RECRUIT_CHAT_URL。
- 调试：`py scripts/chat_followup.py -v --dry-run` 或设 `BOSS_FOLLOWUP_VERBOSE=1`。
- 留窗：`--dry-run` 或 `-v` 时默认会「按 Enter 再关浏览器」；正式无人值守请加 `--no-keep-open`。
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

# 招聘方沟通页（与牛人端 /web/geek/chat 区分）
DEFAULT_CHAT_URL = os.environ.get(
    "BOSS_RECRUIT_CHAT_URL",
    "https://www.zhipin.com/web/boss/chat",
)
# 左列会话行上需包含的文案（Boss 若改版可改环境变量）
FOLLOWUP_ROW_MARKER = os.environ.get("BOSS_FOLLOWUP_ROW_TEXT", "继续沟通").strip() or "继续沟通"
# 登录后再等几秒让左侧列表渲染（含 iframe 内）
FOLLOWUP_LIST_WAIT_SEC = float(os.environ.get("BOSS_FOLLOWUP_LIST_WAIT_SEC", "4"))
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


def get_profile_dir() -> Path:
    p = SCRIPT_DIR / "recruit_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def session_key(name: str, preview: str) -> str:
    safe = re.sub(r"\s+", " ", (name or "未知").strip())[:40]
    h = hashlib.md5(f"{safe}|{preview[:80]}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{safe}|{h}"


def guess_topic(snippet: str, keywords: List[str]) -> str:
    for kw in keywords:
        if kw and kw in snippet:
            return kw
    return "岗位相关"


def build_message(
    cfg: Dict[str, Any],
    round_idx: int,
    candidate_name: str,
    chat_snippet: str,
) -> str:
    """round_idx: 本轮对该会话是第几条跟进（0=首条跟进）。"""
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


def _patch_persistent_context() -> None:
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


def _launch_browser(*, verbose: bool = False):
    random.seed(FIXED_SEED)
    from camoufox.fingerprints import generate_fingerprint

    prof = get_profile_dir().resolve()
    print(f"[followup] 持久化 profile 目录: {prof}")
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


def _locator_roots_chat(page, *, verbose: bool = False, quiet: bool = False):
    """主文档 + 所有子 frame（沟通列表常在 iframe 内，仅搜 page 会 0 条）。"""
    roots = []
    try:
        mf = page.main_frame
        roots.append(mf)
        for fr in page.frames:
            if fr != mf and fr not in roots:
                roots.append(fr)
    except Exception as ex:
        if verbose:
            print(f"[followup][dbg] _locator_roots_chat 异常: {ex!r}")
        pass
    out = roots if roots else [page.main_frame]
    if not quiet:
        print(f"[followup] 可搜索的 frame 数: {len(out)}（主文档 + 子 iframe）")
    if verbose:
        for i, r in enumerate(out):
            try:
                u = (r.url or "")[:160]
                nm = getattr(r, "name", "") or ""
            except Exception:
                u, nm = "(url?)", ""
            print(f"[followup][dbg]   root[{i}] name={nm!r} url={u}")
    return out


def _find_continue_rows_in_root(root, marker: str, *, verbose: bool = False):
    """在单个 Page 或 Frame 根下查找含 marker 的列表行。"""
    patterns = (
        '[class*="geek-item"]',
        '[class*="session"]',
        '[class*="chat-item"]',
        '[class*="conversation"]',
        '[class*="dialog-item"]',
        "li",
    )
    for sel in patterns:
        try:
            loc = root.locator(sel).filter(has_text=marker)
            cnt = loc.count()
            if verbose:
                print(f"[followup][dbg]     selector={sel!r} filter(has_text={marker!r}) -> count={cnt}")
            if cnt > 0:
                if verbose:
                    print(f"[followup][dbg]     选用 selector={sel!r}（首条匹配）")
                return loc
        except Exception as ex:
            if verbose:
                print(f"[followup][dbg]     selector={sel!r} 异常: {ex!r}")
            continue
    return None


def _find_continue_rows_anywhere(
    page,
    marker: str,
    *,
    verbose: bool = False,
    quiet_roots: bool = False,
):
    """
    在主页面与各 iframe 中查找；返回 (locator, root)。
    无匹配时 locator 为占位（count=0），root 为主 frame。
    """
    roots = _locator_roots_chat(page, verbose=verbose, quiet=quiet_roots)
    for ri, root in enumerate(roots):
        if verbose:
            try:
                print(f"[followup][dbg] 在 root[{ri}] 上尝试匹配 marker={marker!r} …")
            except Exception:
                pass
        loc = _find_continue_rows_in_root(root, marker, verbose=verbose)
        if loc is not None:
            try:
                n = loc.count()
            except Exception:
                n = -1
            if not quiet_roots or verbose:
                print(f"[followup] 在 root[{ri}] 命中列表行，当前 count≈{n}")
            if verbose:
                try:
                    print(f"[followup][dbg] 命中 root url={(root.url or '')[:200]}")
                except Exception:
                    pass
            return loc, root
    if not quiet_roots:
        print("[followup] 所有 root 均未匹配到含该文案的行，使用占位 locator（count=0）")
        if verbose:
            print(
                "[followup][dbg] 建议：DevTools 看左侧列表 DOM class；"
                "设 BOSS_FOLLOWUP_ROW_TEXT 为列表上可见短文案。"
            )
    elif verbose:
        print("[followup][dbg] (quiet_roots) 本轮重扫未匹配")
        print(
            "[followup][dbg] 建议：DevTools 看左侧列表 DOM class；"
            "设 BOSS_FOLLOWUP_ROW_TEXT 为列表上可见短文案。"
        )
    try:
        return page.locator("#__boss_recruit_followup_no_rows__"), page.main_frame
    except Exception:
        return page.main_frame.locator("body").filter(has_text="__NO_MATCH__"), page.main_frame


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


def _send_in_chat(page, text: str, dry_run: bool, *, verbose: bool = False) -> bool:
    if dry_run:
        print(f"    [dry-run] 将发送: {text[:200]}...")
        if verbose:
            print(f"    [followup][dbg] dry-run 全文长度={len(text)} 字符")
        return True
    selectors_ta = (
        "textarea",
        "textarea[placeholder*='输入']",
        "textarea[placeholder*='请输入']",
        ".boss-chat-editor textarea",
    )
    for sel in selectors_ta:
        try:
            ta = page.locator(sel).first
            c = ta.count()
            if verbose:
                print(f"    [followup][dbg] 输入框尝试 {sel!r} count={c}")
            if c and ta.is_visible(timeout=2000):
                ta.click(timeout=3000)
                time.sleep(0.2)
                ta.fill(text, timeout=5000)
                time.sleep(0.35)
                print(f"    [followup] 已用输入框选择器: {sel!r}")
                break
        except Exception as ex:
            if verbose:
                print(f"    [followup][dbg] 输入框 {sel!r} 失败: {ex!r}")
            continue
    else:
        print("    [WARN] 未找到输入框")
        return False

    for sbtn in (
        'button:has-text("发送")',
        ".send-btn",
        '[class*="send"]',
        "button.btn-send",
    ):
        try:
            b = page.locator(sbtn).first
            bc = b.count()
            if verbose:
                print(f"    [followup][dbg] 发送按钮尝试 {sbtn!r} count={bc}")
            if bc and b.is_visible(timeout=1500):
                b.click(timeout=5000)
                time.sleep(1.0)
                print(f"    [OK] 已点击发送（{sbtn!r}）")
                return True
        except Exception as ex:
            if verbose:
                print(f"    [followup][dbg] 发送按钮 {sbtn!r} 失败: {ex!r}")
            continue
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
) -> int:
    cfg = load_followup_config()
    state = load_state()
    sessions: Dict[str, Any] = state.setdefault("sessions", {})
    today = _today_key()

    v = verbose or _env_verbose()
    print(f"[followup] 沟通页: {chat_url}")
    print(f"[followup] 本轮最多处理 {max_items} 个「继续沟通」会话（dry_run={dry_run}）")
    print(
        f"[followup] 匹配文案 BOSS_FOLLOWUP_ROW_TEXT={FOLLOWUP_ROW_MARKER!r} | "
        f"列表等待 BOSS_FOLLOWUP_LIST_WAIT_SEC={FOLLOWUP_LIST_WAIT_SEC}"
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

        rows, list_root = _find_continue_rows_anywhere(page, FOLLOWUP_ROW_MARKER, verbose=v)
        n = rows.count()
        print(
            f"[followup] 匹配到约 {n} 条含「{FOLLOWUP_ROW_MARKER}」的列表项"
            f"（主页面+iframe；文案可设 BOSS_FOLLOWUP_ROW_TEXT）"
        )
        if v:
            try:
                lr = (list_root.url or "")[:180]
            except Exception:
                lr = "?"
            print(f"[followup][dbg] 当前用于列表匹配的 list_root.url 预览: {lr}")
        if n == 0:
            print(
                "[followup] 若为 0：① 左侧列表状态文案是否仍为「继续沟通」；"
                "② 试设 BOSS_FOLLOWUP_ROW_TEXT 为页面上实际可见短句；"
                "③ 使用 -v / --dry-run 时会默认留窗，也可显式加 --keep-open。"
            )
            if v:
                print(
                    "[followup][dbg] 你当前有 2 个 frame：主壳常为 /web/chat/index，"
                    "另有一个 about:srcdoc 多为占位/沙箱；若列表在更深层 iframe，"
                    "count 仍可能为 0，需在留窗时用 DevTools 看 Elements。"
                )

        idx = 0
        while sent < max_items and idx < n:
            if v:
                print(f"[followup][dbg] --- 循环 idx={idx} sent={sent} / 首轮 n={n} ---")
            rows, _ = _find_continue_rows_anywhere(
                page, FOLLOWUP_ROW_MARKER, verbose=v, quiet_roots=True
            )
            n_now = rows.count()
            if v and n_now != n:
                print(f"[followup][dbg] 重新扫描后 count 由 {n} 变为 {n_now}")
            if idx >= rows.count():
                if v:
                    print(f"[followup][dbg] idx >= rows.count()，结束循环")
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
            key = session_key(name, preview)
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

            if v:
                pv = (preview or "").replace("\n", " ")[:160]
                print(f"[followup][dbg] 将点击行 name={name!r} 行文本预览: {pv!r}")
            try:
                row.click(timeout=5000)
                if v:
                    print("[followup][dbg] row.click 完成")
            except Exception as e:
                print(f"    [WARN] 点击会话失败 {name}: {e}")
                continue

            time.sleep(2.0)
            try:
                chat_snippet = page.locator("main, .chat-main, .boss-chat-main, body").first.inner_text(
                    timeout=5000
                )[:1200]
                if v:
                    cs = chat_snippet.replace("\n", " ")[:200]
                    print(f"[followup][dbg] chat_snippet 预览: {cs!r}…")
            except Exception as ex:
                chat_snippet = preview
                if v:
                    print(f"[followup][dbg] 读 chat 区失败，用 preview 代替: {ex!r}")

            msg = build_message(cfg, rounds, name, chat_snippet)
            print(f"\n  [会话] {name} | 跟进轮次={rounds} -> 文案预览: {msg[:120]}...")

            ok = _send_in_chat(page, msg, dry_run=dry_run, verbose=v)
            if ok:
                sent += 1
                if not dry_run:
                    rec["last_day"] = today
                    rec["rounds"] = rounds + 1
                    rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
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
                if v:
                    print("[followup][dbg] 已按 Escape 尝试关闭浮层")
            except Exception:
                pass

        print(f"\n[followup] 本轮完成，成功发送 {sent} 条（dry_run={dry_run}）")

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
    args = parser.parse_args()

    if sys.platform == "win32":
        import io

        if sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )

    v_flag = args.verbose or _env_verbose()
    explicit_keep = args.keep_open or _env_keep_open()
    auto_keep = (args.dry_run or v_flag) and not args.no_keep_open and not _env_no_keep_open()
    keep_open_effective = explicit_keep or auto_keep
    if auto_keep and not explicit_keep:
        print(
            "[followup] 因 --dry-run 或 -v：默认留窗，按 Enter 后再关浏览器。"
            "若需跑完立即关，请加 --no-keep-open 或设 BOSS_FOLLOWUP_NO_KEEP_OPEN=1。"
        )

    run_followup(
        max_items=max(0, args.max),
        dry_run=args.dry_run,
        chat_url=args.chat_url,
        keep_open=keep_open_effective,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
