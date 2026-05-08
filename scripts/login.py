"""
Boss Recruit - 扫码登录
使用 Camoufox 打开登录页，用户扫码后保存 cookies
"""

import asyncio
import json
import os
import sys
from camoufox import Camoufox

COOKIE_PATH = os.path.expanduser("~/.agent-browser/auth/boss-recruit.json")

def get_auth_file_path():
    """获取 cookie 存储路径"""
    auth_dir = os.path.expanduser("~/.agent-browser/auth")
    os.makedirs(auth_dir, exist_ok=True)
    return os.path.join(auth_dir, "boss-recruit.json")

async def login():
    """打开登录页，用户扫码后保存 cookies"""
    print("🔐 打开 BOSS 登录页...")

    browser = Camoufox(
        headless=False,  # 显示浏览器窗口以便扫码
        humanize=True,
        geoip=True,
    )

    page = await browser.new_page()

    try:
        # 打开登录页
        await page.goto("https://www.zhipin.com/web/geek/login", wait_until="domcontentloaded")
        print("📱 请使用 Boss App 扫描页面上的二维码登录")

        # 等待登录成功（检测 URL 变化或出现用户信息）
        max_wait = 300  # 5分钟超时
        waited = 0
        check_interval = 2

        while waited < max_wait:
            await asyncio.sleep(check_interval)
            waited += check_interval

            current_url = page.url or ""

            # 检测登录成功：URL 变为 zhipin.com 主站且不含 login
            if "zhipin.com" in current_url and "login" not in current_url:
                print("✅ 登录成功！")
                break

            # 检测是否有二维码过期提示
            try:
                expired_text = await page.query_selector("text=/二维码已失效/")
                if expired_text:
                    print("⚠️ 二维码已失效，等待自动刷新...")
            except:
                pass

        else:
            print("❌ 登录超时，请重试")
            return False

        # 保存 cookies
        cookies = await page.context.cookies()
        auth_file = get_auth_file_path()

        with open(auth_file, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "origin": "https://www.zhipin.com"}, f, ensure_ascii=False, indent=2)

        print(f"✅ Cookies 已保存到: {auth_file}")
        return True

    except Exception as e:
        print(f"❌ 登录失败: {e}")
        return False
    finally:
        await browser.close()

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
        print(f"⚠️ 加载 cookies 失败: {e}")
        return None

if __name__ == "__main__":
    result = asyncio.run(login())
    sys.exit(0 if result else 1)