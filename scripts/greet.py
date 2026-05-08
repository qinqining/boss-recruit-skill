"""
Boss Recruit - 打招呼前简历筛选
核心功能：读取牛人在线简历，用 Agent 分析是否匹配关键词，匹配才打招呼
"""

import asyncio
import json
import os
import sys
import time
from camoufox import Camoufox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.login import load_cookies

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

async def get_resume_text(page, talent_card):
    """从牛人卡片获取在线简历内容"""
    try:
        # 点击牛人卡片打开侧边栏/详情
        await talent_card.click()
        await asyncio.sleep(2)

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
                elem = await page.query_selector(selector)
                if elem:
                    resume_text = await elem.inner_text()
                    if resume_text and len(resume_text) > 50:
                        break
            except:
                pass

        # 关闭详情（如果有关闭按钮）
        try:
            close_btn = await page.query_selector(".close-btn")
            if close_btn:
                await close_btn.click()
                await asyncio.sleep(0.5)
        except:
            pass

        return resume_text

    except Exception as e:
        print(f"    ⚠️ 获取简历失败: {e}")
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

async def greet(talent_names=None, top=10):
    """
    打招呼前筛选 + 发送

    Args:
        talent_names: 指定牛人名字列表，None 表示全部
        top: 最大打招呼数量
    """
    print("🎯 打招呼前筛选开始...")

    cookies = load_cookies()
    if not cookies:
        print("❌ 未登录，请先运行 python scripts/login.py")
        return []

    filter_config = get_filter_config()
    keywords = filter_config["keywords"]
    max_age = filter_config["max_age"]
    print(f"📋 筛选条件: 关键词 {keywords}, 年龄限制 {max_age}岁以下")

    browser = Camoufox(
        headless=False,
        humanize=True,
        geoip=True,
    )

    context = await browser.new_context()
    await context.add_cookies(cookies)
    page = await context.new_page()

    greeted = []
    greet_count = 0

    try:
        # 进入推荐牛人页面
        await page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # 设置筛选条件（参考 search_talent.py）
        # ... 筛选逻辑 ...

        # 遍历牛人列表
        while greet_count < top:
            # 滚动加载
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

            # 获取牛人卡片
            talent_cards = await page.query_selector_all(".talent-card")

            if not talent_cards:
                break

            for i, card in enumerate(talent_cards):
                if greet_count >= top:
                    break

                try:
                    # 获取牛人信息
                    name_elem = await card.query_selector(".name")
                    title_elem = await card.query_selector(".title")

                    name = await name_elem.inner_text() if name_elem else "未知"
                    title = await title_elem.inner_text() if title_elem else "未知"

                    print(f"\n👤 [{greet_count + 1}/{top}] 检查牛人: {name} | {title}")

                    # 获取简历内容
                    resume_text = await get_resume_text(page, card)

                    if resume_text:
                        print(f"    📝 简历长度: {len(resume_text)} 字符")

                        # Agent 分析
                        result = analyze_resume_match(resume_text, keywords, max_age)
                        print(f"    🔍 分析结果: {'匹配' if result['match'] else '不匹配'} - {result['reason']}")

                        if result['match']:
                            # 匹配，点击打招呼按钮
                            try:
                                greet_btn = await card.query_selector(".greet-btn")
                                if greet_btn:
                                    await greet_btn.click()
                                    await asyncio.sleep(5)  # 等待打招呼成功
                                    print(f"    ✅ 已打招呼")
                                    greeted.append({
                                        "name": name,
                                        "title": title,
                                        "matched_keywords": result['reason']
                                    })
                                    greet_count += 1
                                else:
                                    print(f"    ⚠️ 未找到打招呼按钮")
                            except Exception as e:
                                print(f"    ❌ 打招呼失败: {e}")
                        else:
                            print(f"    ⏭️ 跳过（不匹配）")
                    else:
                        print(f"    ⚠️ 无简历内容，跳过")

                    time.sleep(3)  # 间隔

                except Exception as e:
                    print(f"    ⚠️ 处理失败: {e}")
                    continue

        print(f"\n✅ 完成！共打招呼 {len(greeted)} 人")

    except Exception as e:
        print(f"❌ 执行失败: {e}")
    finally:
        await browser.close()

    return greeted

if __name__ == "__main__":
    # 解析参数
    top = 10
    for i, arg in enumerate(sys.argv):
        if arg == "--top" and i + 1 < len(sys.argv):
            top = int(sys.argv[i + 1])

    greeted = asyncio.run(greet(top=top))

    print(f"\n===JSON_START===")
    print(json.dumps(greeted, ensure_ascii=False, indent=2))
    print(f"===JSON_END===")