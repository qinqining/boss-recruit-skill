"""
Boss Recruit - 搜索牛人 + 筛选
进入推荐牛人页面，设置筛选条件，遍历列表
"""

import asyncio
import json
import os
import sys
import time
from camoufox import Camoufox

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.login import load_cookies

COOKIE_PATH = os.path.expanduser("~/.agent-browser/auth/boss-recruit.json")

def get_filter_keywords():
    """打招呼前要筛选的关键词（从配置文件读取或默认）"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                return config.get("filter_keywords", ["SEO", "Google", "搜索引擎", "SEM", "AdWords", "数据分析"])
        except:
            pass
    # 默认关键词
    return ["SEO", "Google", "搜索引擎", "SEM", "AdWords", "数据分析"]

async def search_talent(pages=2):
    """搜索牛人并返回列表"""
    print("🔍 搜索牛人中...")

    # 加载 cookies
    cookies = load_cookies()
    if not cookies:
        print("❌ 未登录，请先运行 python scripts/login.py")
        return []

    browser = Camoufox(
        headless=False,
        humanize=True,
        geoip=True,
    )

    context = await browser.new_context()
    await context.add_cookies(cookies)

    page = await context.new_page()

    try:
        # 进入推荐牛人页面
        await page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        print("⚙️ 设置筛选条件...")

        # 设置筛选：学历本科
        # 注意：BOSS 页面的筛选器是交互式的，需要根据实际页面调整
        # 下面为示例代码，实际选择器需要调试确认

        # 点击学历筛选
        try:
            edu_dropdown = await page.query_selector("[data-role='edu']")
            if edu_dropdown:
                await edu_dropdown.click()
                await asyncio.sleep(0.5)
                # 选择"本科"
                await page.click("text=本科")
                print("  ✓ 学历筛选：本科")
        except Exception as e:
            print(f"  ⚠️ 学历筛选失败: {e}")

        # 设置求职意向筛选（离职-随时到岗、在职-考虑机会、在职-月内到岗）
        try:
            intent_dropdown = await page.query_selector("[data-role='jobIntent']")
            if intent_dropdown:
                await intent_dropdown.click()
                await asyncio.sleep(0.5)
                # 选择三个选项
                for text in ["离职-随时到岗", "在职-考虑机会", "在职-月内到岗"]:
                    try:
                        await page.click(f"text={text}")
                        print(f"  ✓ 求职意向: {text}")
                    except:
                        pass
        except Exception as e:
            print(f"  ⚠️ 求职意向筛选失败: {e}")

        await asyncio.sleep(1)

        # 遍历页面
        talents = []
        for page_num in range(1, pages + 1):
            print(f"\n📄 第 {page_num}/{pages} 页")

            # 滚动加载更多
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # 获取牛人卡片列表
            # 注意：BOSS 可能使用 canvas 渲染，需要根据实际结构调整
            talent_cards = await page.query_selector_all(".talent-card")

            for i, card in enumerate(talent_cards):
                try:
                    # 提取牛人信息
                    name_elem = await card.query_selector(".name")
                    title_elem = await card.query_selector(".title")
                    company_elem = await card.query_selector(".company")

                    name = await name_elem.inner_text() if name_elem else "未知"
                    title = await title_elem.inner_text() if title_elem else "未知"
                    company = await company_elem.inner_text() if company_elem else "未知"

                    print(f"  {i+1}. {name} | {title} | {company}")

                    talents.append({
                        "name": name,
                        "title": title,
                        "company": company,
                        "index": i
                    })
                except Exception as e:
                    print(f"  ⚠️ 解析第 {i+1} 个牛人失败: {e}")

            # 翻页
            if page_num < pages:
                try:
                    next_btn = await page.query_selector(".next")
                    if next_btn:
                        await next_btn.click()
                        await asyncio.sleep(2)
                except:
                    pass

        print(f"\n✅ 共找到 {len(talents)} 个牛人")
        return talents

    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        return []
    finally:
        await browser.close()

if __name__ == "__main__":
    pages = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 2
    talents = asyncio.run(search_talent(pages))
    print(f"\n===JSON_START===")
    print(json.dumps(talents, ensure_ascii=False, indent=2))
    print(f"===JSON_END===")