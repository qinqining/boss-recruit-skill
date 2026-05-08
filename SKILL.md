---
name: boss-recruit
description: "BOSS直聘招聘方自动化：推荐牛人筛选 → 打招呼 → 智能回复判断 → 邀约面试"
---

# Boss Recruit — 招聘方自动化

## Overview

BOSS直聘招聘方自动化，使用 Camoufox 绕过反爬，实现：推荐牛人筛选 → 打招呼前简历分析 → 智能回复判断 → 邀约面试。

Pattern: 扫码登录 → 筛选牛人 → 简历内容分析 → 打招呼 → 多Agent聊天跟进

## When to Use

- 招聘方在 BOSS 直聘上批量打招呼
- 筛选牛人简历（SEO/Google 相关经验）
- 智能判断候选人回复，决定邀约或放弃
- 批量跟进消息，自动邀约面试

## Core Pattern

```
1. 登录 & Session
   └─ Camoufox 打开登录页 → 用户扫码 → 保存 cookies
   └─ 后续运行加载 cookies 自动验证

2. 搜索牛人
   └─ 进入"推荐牛人"页面
   └─ 设置筛选条件

3. 简历筛选（打招呼前）
   └─ 点击牛人卡片获取在线简历
   └─ Agent 分析简历内容是否含 SEO/Google 相关关键词
   └─ 匹配 → 打招呼；不匹配 → 跳过

4. 消息监控 + 智能判断
   └─ 定期检查消息列表
   └─ Agent 分析回复意图
   └─ 强意向 → 发送面试邀请；消极 → pass

5. 安全规则
   └─ Code 36/32 立即停止
   └─ 频率限制防封禁
```

## Prerequisites

```bash
pip install "camoufox[geoip]" && camoufox fetch
```

## 工作流程

### 步骤1：登录

```bash
python scripts/login.py
```

- 打开 BOSS 招聘方登录页
- 用户扫码登录
- 自动保存 cookies 到 `~/.agent-browser/auth/boss-recruit.json`

### 步骤2：搜索牛人

```bash
python scripts/search_talent.py
```

- 进入"推荐牛人"
- 设置筛选：学历本科、求职意向（离职随时到岗/在职考虑机会/在职月内到岗）
- 遍历牛人列表

### 步骤3：打招呼前筛选

```bash
python scripts/greet.py --top 10
```

- 读取每个牛人的在线简历
- Agent 判断是否含 SEO/Google 关键词
- 匹配的发送打招呼

### 步骤4：消息监控 + 智能回复

```bash
python scripts/chat_auto.py --interval 30
```

- 每30秒检查新消息
- Agent 分析回复内容，判断：
  - **invite**：发送面试邀请
  - **continue**：继续跟进
  - **pass**：标记不合适

## 简历筛选规则

Agent 根据简历内容判断（双重条件）：

### ① 关键词匹配（业务结果 + SEO能力 + 运营经验）

| 类别 | 关键词 |
|------|--------|
| 业务结果 | 询盘数、转化率、曝光、点击、流量、排名提升 |
| SEO专业能力 | 关键词研究、内链、外链、内容策略、E-A-T、技术SEO |
| 网站运营 | 独立站、B端、建站、运营、竞品分析 |
| 工具信号 | SEMrush、Google Analytics、GA、Search Console、Ahrefs |

### ② 年龄限制

- **硬门槛**：32岁以下
- 年龄检测：简历中的"32岁"、"年龄32"等模式

**同时满足关键词匹配 + 年龄符合，才打招呼。**

```json
{
  "match": true/false,
  "reason": "匹配: 询盘, SEO, SEMrush | 年龄28岁"
}
```

## 聊天智能判断

Agent 分析候选人回复：

```json
{
  "action": "invite|continue|pass",
  "reply": "Agent要发送的回复内容"
}
```

**判断逻辑**：
- 主动询问薪资/时间/面试流程 → 强意向 → **invite**
- 正面回应但有疑问 → 解答并邀约 → **invite**
- 消极/已读不回/拒绝 → **pass**
- 询问更多信息 → 继续回答 → **continue**

## CLI 命令

```bash
# 登录
python scripts/login.py

# 搜索牛人 + 筛选
python scripts/search_talent.py --pages 2

# 打招呼前筛选 + 发送
python scripts/greet.py --top 10

# 消息监控 + 智能回复
python scripts/chat_auto.py --interval 30

# 一键运行
python scripts/run_pipeline.py --keywords SEO --top 5
```

## 安全规则

| Code | 含义 | 操作 |
|------|------|------|
| 0 | 成功 | 继续 |
| 37 | 环境异常 | 自动生成zp_stoken重试1次 |
| 36 | 账户异常 | **立即停止**，用户手动验证 |
| 32 | 账户封禁 | **立即停止**，用户手动发消息恢复 |

**遇到 Code 36/32 立即停止所有自动化。**

## 频率限制

- 页面操作间隔：3-5秒
- 打招呼间隔：5秒
- 消息检查间隔：30秒
- 单次最大打招呼：20条

## 文件结构

```
boss-recruit/
├── SKILL.md                    # 主文档
├── CLAUDE.md                   # Agent 指令
├── boss                        # CLI 入口
├── scripts/
│   ├── login.py                # 扫码登录
│   ├── search_talent.py        # 搜索牛人 + 筛选
│   ├── greet.py                # 打招呼前筛选
│   ├── chat_auto.py           # 消息监控 + 智能判断
│   └── run_pipeline.py         # 一键 pipeline
└── docs/
    └── REVERSE_ENGINEERING.md  # 反爬研究
```

## 技术要点

### Camoufox 反爬

使用 C++ 级 Firefox 指纹修改，绕过 BOSS 4层反爬：
- TLS/JA3 指纹
- Canvas/WebGL 指纹
- Behavior Humanizer
- GeoIP 时区/语言

### 页面结构

BOSS 招聘方"推荐牛人"页面：
- Tab 切换：推荐牛人
- 筛选条件：学历（本科）、求职意向
- 牛人列表：`.job-card` 或 canvas 渲染
- 打招呼按钮：`.btn-greet` 或文字"打招呼"
- 在线简历：通过点击牛人卡片打开侧边栏

### 简历获取

BOSS 牛人简历可能是：
1. 在线展示（侧边栏）
2. 附件 PDF（可下载）
3. 图片

优先获取在线文本内容进行分析。