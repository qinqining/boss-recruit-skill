"""
Boss Recruit - 消息监控 + 智能回复
定期检查消息列表，Agent 分析回复内容，决定：invite / continue / pass
"""

import asyncio
import json
import os
import sys
import time
from camoufox import Camoufox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.login import load_cookies

# 默认检查间隔（秒）
DEFAULT_INTERVAL = 30

def analyze_reply(reply_text):
    """
    Agent 分析回复内容，判断意图

    Returns:
        {"action": "invite|continue|pass", "reply": "Agent要发送的回复"}
    """
    if not reply_text:
        return {"action": "continue", "reply": ""}

    reply_lower = reply_text.lower()

    # 强意向信号：问薪资/时间/面试
    strong_intent_keywords = [
        "薪资", "工资", "待遇", "面试时间", "什么时候", "怎么联系",
        "电话", "微信", "方便", "最快", "offer", "薪资范围"
    ]

    # 消极信号
    passive_keywords = [
        "不需要", "不需要招人", "已找到", "暂时不考虑",
        "不好意思", "抱歉", "不需要谢谢", "已经", "不考虑"
    ]

    # 检查强意向
    for kw in strong_intent_keywords:
        if kw in reply_lower:
            return {
                "action": "invite",
                "reply": "您好！我们觉得您很适合这个岗位，方便安排一次面试吗？请问您最近哪天有时间？"
            }

    # 检查消极信号
    for kw in passive_keywords:
        if kw in reply_lower:
            return {
                "action": "pass",
                "reply": ""
            }

    # 有疑问，需要继续沟通
    return {
        "action": "continue",
        "reply": "您好，感谢您的回复！如果您对我们的职位有兴趣，可以进一步沟通~"
    }

async def check_and_reply(page, interval=30):
    """
    检查消息列表，对新消息进行智能回复

    Args:
        interval: 检查间隔（秒）
    """
    print(f"🔔 开始监控消息，间隔 {interval} 秒...")

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

    processed_messages = set()  # 已处理的消息ID

    try:
        # 进入消息页面
        await page.goto("https://www.zhipin.com/web/geek/chat", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        print("✅ 已进入消息页面")

        while True:
            try:
                # 获取对话列表
                chat_items = await page.query_selector_all(".chat-item")

                if not chat_items:
                    print(f"  ⏰ [{time.strftime('%H:%M:%S')}] 暂无新消息")
                else:
                    for item in chat_items:
                        try:
                            # 获取消息ID/内容用于去重
                            msg_elem = await item.query_selector(".last-message")
                            if not msg_elem:
                                continue

                            msg_text = await msg_elem.inner_text()
                            msg_hash = hash(msg_text)

                            # 跳过已处理的消息
                            if msg_hash in processed_messages:
                                continue

                            # 获取聊天对象名字
                            name_elem = await item.query_selector(".name")
                            name = await name_elem.inner_text() if name_elem else "未知"

                            print(f"\n💬 新消息 from {name}: {msg_text[:50]}...")

                            # Agent 分析
                            result = analyze_reply(msg_text)
                            print(f"   📊 判断: {result['action']}")

                            if result['action'] == 'invite':
                                # 发送面试邀请
                                try:
                                    await item.click()
                                    await asyncio.sleep(1)

                                    # 找到输入框
                                    input_box = await page.query_selector("textarea[name='message']")
                                    if input_box:
                                        await input_box.fill(result['reply'])
                                        await asyncio.sleep(0.5)

                                        # 点击发送
                                        send_btn = await page.query_selector(".send-btn")
                                        if send_btn:
                                            await send_btn.click()
                                            print(f"   ✅ 已发送: {result['reply']}")
                                        else:
                                            print(f"   ⚠️ 未找到发送按钮")
                                    else:
                                        print(f"   ⚠️ 未找到输入框")

                                    processed_messages.add(msg_hash)

                                except Exception as e:
                                    print(f"   ❌ 回复失败: {e}")

                            elif result['action'] == 'pass':
                                print(f"   ⏭️ 标记为不合适")
                                processed_messages.add(msg_hash)

                            elif result['action'] == 'continue':
                                # 继续跟进
                                try:
                                    await item.click()
                                    await asyncio.sleep(1)

                                    input_box = await page.query_selector("textarea[name='message']")
                                    if input_box:
                                        await input_box.fill(result['reply'])
                                        await asyncio.sleep(0.5)

                                        send_btn = await page.query_selector(".send-btn")
                                        if send_btn:
                                            await send_btn.click()
                                            print(f"   ✅ 已发送: {result['reply']}")

                                    processed_messages.add(msg_hash)

                                except Exception as e:
                                    print(f"   ❌ 回复失败: {e}")

                        except Exception as e:
                            print(f"   ⚠️ 处理消息失败: {e}")
                            continue

            except Exception as e:
                print(f"⚠️ 检查消息失败: {e}")

            # 等待下一次检查
            print(f"\n  💤 等待 {interval} 秒...")
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        print("\n⏹️ 用户停止监控")
    finally:
        await browser.close()

if __name__ == "__main__":
    interval = DEFAULT_INTERVAL

    for i, arg in enumerate(sys.argv):
        if arg == "--interval" and i + 1 < len(sys.argv):
            interval = int(sys.argv[i + 1])

    print(f"启动消息监控，间隔 {interval} 秒 (Ctrl+C 停止)")
    asyncio.run(check_and_reply(interval=interval))