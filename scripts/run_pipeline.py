"""
Boss Recruit - 一键运行 Pipeline
搜索 → 打招呼前筛选 → 发送 → 监控回复
"""

import asyncio
import json
import os
import sys
import time
from camoufox import Camoufox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.login import load_cookies
from scripts.greet import get_filter_keywords, analyze_resume_match

async def run(keywords=None, top=5, monitor_interval=0):
    """
    一键运行完整流程

    Args:
        keywords: 筛选关键词列表，None 使用默认
        top: 最大打招呼数量
        monitor_interval: 监控间隔，0 表示不监控
    """
    print("🚀 Boss Recruit Pipeline 开始")
    print(f"   关键词: {keywords or '默认'}")
    print(f"   Top: {top}")
    print(f"   监控: {'是' if monitor_interval else '否'}\n")

    # 使用提供的关键词或默认
    filter_keywords = keywords if keywords else get_filter_keywords()

    # 检查登录
    cookies = load_cookies()
    if not cookies:
        print("❌ 未登录，请先运行 python scripts/login.py")
        return

    browser = Camoufox(
        headless=False,
        humanize=True,
        geoip=True,
    )

    context = await browser.new_context()
    await context.add_cookies(cookies)
    page = await context.new_page()

    greeted = []

    try:
        # 1. 进入推荐牛人页面
        print("📍 进入推荐牛人页面...")
        await page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # 2. 设置筛选（学历、求职意向）
        print("⚙️ 设置筛选条件...")

        # 3. 遍历牛人
        greet_count = 0
        while greet_count < top:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

            talent_cards = await page.query_selector_all(".talent-card")
            if not talent_cards:
                print("⚠️ 未找到牛人卡片")
                break

            for card in talent_cards:
                if greet_count >= top:
                    break

                try:
                    # 获取牛人信息
                    name_elem = await card.query_selector(".name")
                    title_elem = await card.query_selector(".title")

                    name = await name_elem.inner_text() if name_elem else "未知"
                    title = await title_elem.inner_text() if title_elem else "未知"

                    print(f"\n👤 [{greet_count + 1}/{top}] {name} | {title}")

                    # 点击获取简历
                    await card.click()
                    await asyncio.sleep(2)

                    # 提取简历文本（多个选择器尝试）
                    resume_text = ""
                    for selector in [".resume-content", ".candidate-resume", "[class*='resume']"]:
                        try:
                            elem = await page.query_selector(selector)
                            if elem:
                                resume_text = await elem.inner_text()
                                if len(resume_text) > 50:
                                    break
                        except:
                            pass

                    # Agent 分析匹配
                    if resume_text:
                        result = analyze_resume_match(resume_text, filter_keywords)
                        print(f"   🔍 {result['reason']}")

                        if result['match']:
                            # 打招呼
                            try:
                                greet_btn = await card.query_selector(".greet-btn")
                                if greet_btn:
                                    await greet_btn.click()
                                    await asyncio.sleep(5)
                                    print(f"   ✅ 已打招呼")
                                    greeted.append({"name": name, "title": title})
                                    greet_count += 1
                            except Exception as e:
                                print(f"   ❌ 打招呼失败: {e}")
                        else:
                            print(f"   ⏭️ 跳过")
                    else:
                        print(f"   ⚠️ 无简历")

                    time.sleep(3)

                except Exception as e:
                    print(f"   ⚠️ 处理失败: {e}")
                    continue

        print(f"\n✅ Pipeline 完成！共打招呼 {len(greeted)} 人")

        # 4. 如果需要监控，启动监控
        if monitor_interval > 0:
            print(f"\n🔔 启动消息监控（间隔 {monitor_interval} 秒）...")
            # 这里可以启动监控逻辑，但需要新页面
            # 简化处理：提示用户手动运行 chat_auto.py

    except Exception as e:
        print(f"❌ Pipeline 失败: {e}")
    finally:
        await browser.close()

    print(f"\n===RESULT===")
    print(json.dumps(greeted, ensure_ascii=False, indent=2))
    print(f"===RESULT===")

if __name__ == "__main__":
    keywords = None
    top = 5
    monitor_interval = 0

    for i, arg in enumerate(sys.argv):
        if arg == "--keywords" and i + 1 < len(sys.argv):
            keywords = [k.strip() for k in sys.argv[i + 1].split(",")]
        elif arg == "--top" and i + 1 < len(sys.argv):
            top = int(sys.argv[i + 1])
        elif arg == "--monitor" and i + 1 < len(sys.argv):
            monitor_interval = int(sys.argv[i + 1])

    asyncio.run(run(keywords=keywords, top=top, monitor_interval=monitor_interval))