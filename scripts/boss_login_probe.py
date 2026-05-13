"""
Boss 直聘 Web 端：登录态 DOM 探测（greet / login 共用）。

说明：
- `.header-user-avatar` 常为图片或无文本节点，旧逻辑用 inner_text>0 会导致该选择器形同虚设，
  页面稍慢时仅依赖 `.user-name` 易误判「未登录」而反复跳转登录页。
- 头像仅在非「登录 URL」时作为辅助信号，降低登录页误报。
"""

from __future__ import annotations

import time
from typing import Optional


def _on_boss_login_url(url: str) -> bool:
    u = (url or "").lower()
    return "/web/geek/login" in u or "security-check" in u or "_security_check" in u


def check_login(page) -> bool:
    """
    通过顶栏 DOM 判断是否已登录（招聘方 / geek 站通用顶栏类名）。
    """
    try:
        url = page.url or ""

        # 1) 用户名有可见文字（主信号）
        try:
            loc = page.locator(".user-name").first
            if loc.count() > 0 and loc.is_visible(timeout=2000):
                text = (loc.inner_text() or "").strip()
                if len(text) > 0:
                    return True
        except Exception:
            pass

        # 2) 头像可见（常为 img，无 inner_text；登录 URL 上不用此条，避免误报）
        if not _on_boss_login_url(url):
            try:
                av = page.locator(".header-user-avatar").first
                if av.count() > 0 and av.is_visible(timeout=1500):
                    return True
            except Exception:
                pass

        return False
    except Exception:
        return False


def probe_logged_in(
    page,
    *,
    retries: int = 6,
    delay_sec: float = 1.25,
    label: str = "",
) -> bool:
    """
    多次探测，缓解 SPA 首屏渲染慢导致的假「未登录」。
    """
    prefix = f"[login-probe{(':' + label) if label else ''}]"
    for i in range(retries):
        if check_login(page):
            if i > 0:
                print(f"{prefix} 第 {i + 1} 次探测成功（此前可能为渲染延迟）")
            return True
        if i < retries - 1:
            time.sleep(delay_sec)
    print(
        f"{prefix} 连续 {retries} 次未检测到登录顶栏"
        f"（当前 URL: {(page.url or '')[:120]}）"
    )
    return False
