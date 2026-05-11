"""
Boss Recruit - 扫码登录
使用 Camoufox 打开登录页，用户扫码后保存 cookies

Cookie 与指纹绑定:
  开启 persistent_context=True + 固定 user_data_dir + 固定 seed，
  实现扫码一次永久免登（类似 boss-auto-job 的机制）。
"""

import asyncio
import json
import os
import sys
import time
import random
from pathlib import Path
from camoufox import Camoufox
from camoufox import launch_options

COOKIE_PATH = os.path.expanduser("~/.agent-browser/auth/boss-recruit.json")
FIXED_SEED = "boss-recruit-agent-2026"  # 固定种子保证指纹一致

def get_auth_file_path():
    """获取 cookie 存储路径"""
    auth_dir = os.path.expanduser("~/.agent-browser/auth")
    os.makedirs(auth_dir, exist_ok=True)
    return os.path.join(auth_dir, "boss-recruit.json")

def get_profile_dir():
    """获取 Firefox profile 目录（持久化 Cookie）"""
    profile_dir = Path(__file__).parent.parent / "recruit_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir

def check_login(page) -> bool:
    """DOM 元素检测登录状态（不依赖 URL）
    必须同时满足：元素存在 AND 元素有实际内容（不是空壳）"""
    try:
        selectors = [".user-name", ".header-user-avatar"]
        for sel in selectors:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el:
                    text = el.inner_text().strip() if el.inner_text() else ""
                    # 有实际用户名内容才算登录
                    if len(text) > 0:
                        return True
            except:
                continue
        return False
    except:
        return False

def login():
    """打开登录页，用户扫码后保存 cookies（sync API）"""
    print("[LOGIN] Opening BOSS login page...")

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

    try:
        # 打开登录页
        page.goto("https://www.zhipin.com/web/geek/login", wait_until="domcontentloaded")
        print("[LOGIN] Please scan QR with Boss App")

        # 等待登录成功（DOM 检测，不依赖 URL）
        max_wait = 300
        waited = 0
        while waited < max_wait:
            time.sleep(2)
            waited += 2

            # 安全检查页处理
            url = page.url or ""
            if "_security_check" in url or "security-check" in url:
                print(f"[login] {waited}s - Security check, waiting 10s...")
                time.sleep(10)
                continue

            if check_login(page):
                print(f"[OK] Login success! URL: {page.url}")
                break

            if waited % 20 == 0:
                print(f"[login] {waited}s - URL: {url[:60]}")

        else:
            print("[FAIL] Login timeout")
            return False

        # 保存 cookies
        cookies = context.cookies()
        auth_file = get_auth_file_path()
        with open(auth_file, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "origin": "https://www.zhipin.com"}, f, ensure_ascii=False, indent=2)

        print(f"[OK] Cookies saved: {auth_file}")
        print(f"[OK] Seed: {FIXED_SEED}, Profile: {get_profile_dir()}")
        return True

    except Exception as e:
        print(f"[FAIL] Login failed: {e}")
        return False
    finally:
        browser.__exit__(None, None, None)

def load_cookies():
    """加载已保存的 cookies"""
    auth_file = get_auth_file_path()
    if not os.path.exists(auth_file):
        return None
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("cookies", [])
    except Exception as e:
        print(f"[WARN] 加载 cookies 失败: {e}")
        return None

if __name__ == "__main__":
    result = login()
    sys.exit(0 if result else 1)