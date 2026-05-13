#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招聘方：沟通列表中「继续沟通」会话的智能跟进（短对话：地址通勤、经历追问、索要简历等）。

- 与 greet.py 一致：Camoufox 持久化 profile（recruit_profile）、固定 seed、排除 UBO。
- 默认打开招聘端沟通页；若贵司实际入口不同，设环境变量 BOSS_RECRUIT_CHAT_URL。
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

from boss_login_probe import check_login, probe_logged_in

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


def _launch_browser():
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
    return browser, page


def _find_continue_rows(page):
    """左列会话列表：含「继续沟通」的条目（多组选择器兜底）。"""
    patterns = [
        '[class*="geek-item"]',
        '[class*="session"]',
        '[class*="chat-item"]',
        "li",
    ]
    for sel in patterns:
        try:
            loc = page.locator(sel).filter(has_text="继续沟通")
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return page.locator("#__boss_recruit_followup_no_rows__")


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


def _send_in_chat(page, text: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"    [dry-run] 将发送: {text[:200]}...")
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
            if ta.count() and ta.is_visible(timeout=2000):
                ta.click(timeout=3000)
                time.sleep(0.2)
                ta.fill(text, timeout=5000)
                time.sleep(0.35)
                break
        except Exception:
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
            if b.count() and b.is_visible(timeout=1500):
                b.click(timeout=5000)
                time.sleep(1.0)
                print("    [OK] 已点击发送")
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Enter")
        time.sleep(1.0)
        print("    [OK] 已尝试 Enter 发送")
        return True
    except Exception:
        return False


def run_followup(max_items: int, dry_run: bool, chat_url: str) -> int:
    cfg = load_followup_config()
    state = load_state()
    sessions: Dict[str, Any] = state.setdefault("sessions", {})
    today = _today_key()

    print(f"[followup] 沟通页: {chat_url}")
    print(f"[followup] 本轮最多处理 {max_items} 个「继续沟通」会话（dry_run={dry_run}）")

    browser = None
    sent = 0
    try:
        browser, page = _launch_browser()
        page.goto(chat_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        time.sleep(2.0)

        if not probe_logged_in(page, label="chat"):
            print("[login] 未登录或探测失败，请先在同一 profile 下执行 py scripts/login.py 扫码")
            return 0

        time.sleep(1.5)
        rows = _find_continue_rows(page)
        n = rows.count()
        print(f"[followup] 匹配到约 {n} 条含「继续沟通」的列表项")

        idx = 0
        while sent < max_items and idx < n:
            rows = _find_continue_rows(page)
            if idx >= rows.count():
                break
            row = rows.nth(idx)
            idx += 1
            try:
                if not row.is_visible(timeout=1500):
                    continue
            except Exception:
                continue

            name, preview = _row_name_preview(row)
            key = session_key(name, preview)
            rec = sessions.get(key) or {}
            last_day = rec.get("last_day")
            rounds = int(rec.get("rounds", 0))
            if last_day == today and rounds >= int(os.environ.get("BOSS_FOLLOWUP_MAX_PER_DAY", "2")):
                print(f"    [skip] {name} 今日跟进已达上限")
                continue

            try:
                row.click(timeout=5000)
            except Exception as e:
                print(f"    [WARN] 点击会话失败 {name}: {e}")
                continue

            time.sleep(2.0)
            try:
                chat_snippet = page.locator("main, .chat-main, .boss-chat-main, body").first.inner_text(
                    timeout=5000
                )[:1200]
            except Exception:
                chat_snippet = preview

            msg = build_message(cfg, rounds, name, chat_snippet)
            print(f"\n  [会话] {name} | 跟进轮次={rounds} -> 文案预览: {msg[:120]}...")

            ok = _send_in_chat(page, msg, dry_run=dry_run)
            if ok:
                sent += 1
                if not dry_run:
                    rec["last_day"] = today
                    rec["rounds"] = rounds + 1
                    rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    sessions[key] = rec
                    save_state(state)
                pause = float(cfg.get("pause_after_send_sec") or 6)
                print(f"    [followup] 等待 {pause:.0f}s 防风控…")
                time.sleep(pause)
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass

        print(f"\n[followup] 本轮完成，成功发送 {sent} 条（dry_run={dry_run}）")
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
    args = parser.parse_args()

    if sys.platform == "win32":
        import io

        if sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )

    run_followup(max_items=max(0, args.max), dry_run=args.dry_run, chat_url=args.chat_url)


if __name__ == "__main__":
    main()
