# BOSS 招聘方反爬研究

## 概述

BOSS 直聘对招聘方也有反爬机制，主要包括：
1. **TLS 指纹检测**：识别非浏览器 TLS 握手
2. **浏览器指纹**：Canvas、WebGL、Font 等
3. **行为检测**：鼠标轨迹、键盘节奏
4. **风控验证**：验证码、人机验证

## 招聘方页面特点

### 推荐牛人页面
- URL: `https://www.zhipin.com/web/geek/recommend`
- 牛人列表可能使用 canvas 渲染
- 需要点击卡片获取详情/简历

### 打招呼机制
- 默认消息，无法自定义文本
- 有每日上限（通常 20-50 条）
- 频繁打招呼可能触发验证

### 消息页面
- URL: `https://www.zhipin.com/web/geek/chat`
- 实时消息推送
- 可以获取对话历史

## 反爬策略

### Camoufox 配置
```python
browser = Camoufox(
    headless=False,
    humanize=True,     # 模拟人类鼠标/键盘行为
    geoip=True,        # 自动设置时区/语言
)
```

### 关键参数
- `humanize=True`：C++ HumanCursor 模拟真实鼠标轨迹
- `geoip=True`：根据 IP 自动设置 timezone/locale
- `headless=False`：首次建议显示浏览器便于调试

### 绕过检测
1. **TLS 指纹**：Camoufox 内置修改
2. **Canvas/WebGL**：C++ 层修改，无法被 JS 检测
3. **行为指纹**：humanize=True 自动模拟

## 常见问题

### 1. 登录二维码一直刷新
- 可能是 IP 被限制
- 尝试更换网络或使用 VPN

### 2. 打招呼按钮点击无反应
- 可能是页面未完全加载
- 添加 wait_until="networkidle"

### 3. 消息列表为空
- 检查是否正确登录（ cookies 有效）
- 可能是 API 请求频率限制

### 4. Code 36/32 处理
- 立即停止所有操作
- 用户手动在浏览器完成验证
- 然后重新运行脚本

## 安全规则

| Code | 含义 | 操作 |
|------|------|------|
| 0 | 成功 | 继续 |
| 37 | 环境异常 | 重试 1 次 |
| 36 | 账户异常 | **停止**，用户验证 |
| 32 | 账户封禁 | **停止**，用户手动恢复 |

## 技术参考

- Camoufox 文档：https://github.com/nicheias/camoufox
- Playwright API：https://playwright.dev/python/
- BOSS 反爬研究：参考 boss-auto-job 项目