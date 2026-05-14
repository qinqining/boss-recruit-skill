---
name: boss-recruit
description: "BOSS直聘招聘方自动化（Camoufox：筛选→打招呼→跟进）；离线：简历筛选评分、面试方案（十问+Word交付）"
---

# Boss Recruit — 招聘方自动化

## Overview

BOSS直聘招聘方自动化（Camoufox）：推荐牛人筛选 → 打招呼前规则判定 → 沟通列表跟进；另含**离线**任务：本地简历库筛选评分、按素质项与候选人材料生成面试方案（十问），**面试方案正式交付须含 Word（`.docx`）**；用户确认定稿后简历归档至 `面试方案生成任务/已处理简历/`，见 `面试方案生成任务/任务.md`。

Pattern: 扫码登录 → 筛选牛人 → 简历内容分析 → 打招呼 → 多Agent聊天跟进；离线任务为读 Markdown/PDF，无 BOSS 浏览器步骤。

## When to Use

- 招聘方在 BOSS 直聘上批量打招呼
- 筛选牛人简历（SEO/Google 相关经验）
- 智能判断候选人回复，决定邀约或放弃
- 批量跟进消息，自动邀约面试
- **离线**：简历筛选评分、按 JD 与案例打分、输出与按日期归档（见 `简历筛选评分任务/`）
- **离线**：面试方案（十问）、素质项与助理向校准；**须输出 `.docx`**；用户确认方案后归档简历至 `已处理简历/`（见 `面试方案生成任务/任务.md` 与 `scripts/interview_plan_md_to_docx.py`）

## 离线任务（无 Camoufox）

与 BOSS 页面自动化独立；Agent 读任务说明与本地文件即可。

### 简历筛选评分

- **入口**：`简历筛选评分任务/简历筛选任务.md`
- **输入**：`简历筛选评分任务/技术SEO岗位要求.md`（可换岗）、`简历筛选评分任务/需筛选简历/`、`简历筛选评分任务/优秀简历案例/`、`简历筛选评分任务/不合格简历案例/`；**PDF 先** `py scripts/pdf_resume_to_md.py …pdf` **转 md** 再读（`pip install pymupdf`）
- **输出**：`简历筛选评分任务/输出/` 下带日期的评分报告；已处理简历移入 `简历筛选评分任务/已归档/YYYY-MM-DD/`
- 目录说明：`简历筛选评分任务/README.md`

### 面试方案生成

- **入口**：`面试方案生成任务/任务.md`；**必读**：`面试方案生成任务/岗位层级说明.md`（素质表为运营档、默认面试 **SEO 助理** 时的校准规则）
- **素质表（Markdown）**：`面试方案生成任务/岗位素质项/01_岗位与JD门槛.md`、`02_素质分档与面试母题.md`
- **候选人**：默认 `面试方案生成任务/候选人简历/`；**BOSS 下载多为 PDF**，Agent 宜先 `py scripts/pdf_resume_to_md.py xxx.pdf` 生成同名 `.md` 再阅读（`pip install pymupdf`）；扫描件需 OCR
- **输出**：`面试方案生成任务/输出/` 下**必须**含带日期的 **`面试方案_*.docx`**（正式交付）；可先写同名 `.md` 再运行 `py scripts/interview_plan_md_to_docx.py …md` 生成 Word（需 `pip install python-docx`）
- **归档**：用户**明确确认**面试方案定稿后，将本次对应候选人在 `候选人简历/` 下的简历文件（`.pdf`/同名 `.md` 等）**移动**至 `面试方案生成任务/已处理简历/`（详见 `面试方案生成任务/任务.md`「用户确认后」）
- **问题设计约束**：统筹规划不作为主要考察素质项（助理岗位侧重执行配合）；问题应多围绕候选人 **SEO 相关工作经历**（关键词研究、内容优化、数据监控、技术 SEO 等），避免跨行业类比（如用学生会、土建施工经历代替 SEO 经历提问）

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

运行本仓库 CLI 与脚本时，文档命令统一为 **`py`**（Windows Python Launcher）；避免 `python`/`python3` 未在 PATH 中导致失败。非 Windows 可用 `python3` 等价替换。

## 工作流程

### 步骤1：登录

```bash
py scripts/login.py
```

- 打开 BOSS 招聘方登录页
- 用户扫码登录
- 自动保存 cookies 到 `~/.agent-browser/auth/boss-recruit.json`

### 步骤2：搜索牛人

```bash
py scripts/search_talent.py
```

- 进入"推荐牛人"
- 设置筛选：学历本科、求职意向（离职随时到岗 / 在职月内到岗；不含「在职-考虑机会」——脚本不对其打招呼）
- 遍历牛人列表

### 步骤3：打招呼前筛选（命令行 / CLI）

在技能根目录 `boss-recruit` 下执行：

```bash
# 推荐：统一 CLI（不写参数则默认本轮最多成功打招呼 20 人，不匹配则跳过继续扫）
py boss greet

# 自定义上限：例如只要 10 个
py boss greet --top 10

# 或直接跑脚本（等价）；也可用 BOSS_GREET_TOP=30 改默认
py scripts/greet.py

# 不写 txt 报告（仅控制台 + llm_audit_log.jsonl）
py scripts/greet.py --no-report
# 或环境变量：BOSS_GREET_NO_REPORT=1
```

- 读取每个牛人的在线简历，**规则引擎**判定是否匹配（见脚本内 `RULE_*` 常量）
- 匹配则按间隔打招呼；脚本会在 **iframe 推荐列表内滚动**、拉末卡入屏以尝试加载更多，直到凑满 `top` 或连续若干次滚动后卡片数仍不增加（`BOSS_GREET_LIST_SCROLL_STALL`，默认 5）
- 每条判定写入 **`reports/greet_rule_report_run序号_YYYYMMDD_HHMMSS.txt`**（UTF-8）；文首/文末含 **报告日期** 与 **第 N 次运行**（序号在 `reports/greet_run_index.json`）
- 审计 JSONL 仍为根目录 `llm_audit_log.jsonl`
- **风控节奏（默认偏保守）**：`BOSS_GREET_SAFE_PACE=1`（默认）拉长匹配后等待、卡片间隔与列表滚动停顿，并对间隔加随机抖动 `BOSS_GREET_JITTER_RATIO`；封禁恢复期可再降低 `BOSS_GREET_TOP`。设为 `BOSS_GREET_SAFE_PACE=0` 可略加快（更易触发风控）。可调：`GREET_AFTER_MATCH_WAIT_MIN`/`MAX`、`GREET_COOLDOWN_BETWEEN_CARDS_SEC`、`GREET_COOLDOWN_AFTER_SUCCESS_SEC`、`POST_CARD_CLICK_PAUSE_SEC`、`GREET_LIST_SCROLL_PAUSE_SEC`。

### 步骤4：沟通列表「继续沟通」智能跟进（发消息后）

```bash
py boss followup
py boss followup --max 3
py boss followup --dry-run
```

- 与 `greet` **同一 Camoufox 持久化目录** `recruit_profile`，避免登录态不一致。  
- 默认打开 **`/web/boss/chat`**；若入口不同，设置环境变量 `BOSS_RECRUIT_CHAT_URL`。  
- 在列表中查找含 **「继续沟通」** 的会话，依次打开并发送**短跟进**（公司地址/通勤、经历追问、索要 PDF 简历），轮次与上限见根目录 **`AGENTS.md`**。  
- 状态记录在 `reports/followup_state.json`（防同日对同一人刷太多条）。

更多 Agent 话术与安全边界见 **`AGENTS.md`**。

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
py scripts/login.py

# 搜索牛人 + 筛选（页数）
py boss search 2

# 打招呼前筛选 + 发送（默认凑满 20 个匹配）
py scripts/greet.py

# 沟通列表跟进（与 greet 同 profile）
py boss followup
py boss followup --dry-run

# 一键运行
py scripts/run_pipeline.py --keywords SEO --top 5
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
- 跟进发送间隔：见 `config.json` → `followup.pause_after_send_sec`（默认约 6 秒）
- 单次最大打招呼：20条

## 文件结构

```
boss-recruit/
├── SKILL.md
├── CLAUDE.md
├── AGENTS.md
├── boss
├── 简历筛选评分任务/          # 离线：简历打分、输出、归档
├── 面试方案生成任务/          # 离线：面试方案（十问）、岗位素质项 md；已确认方案后简历归档至 已处理简历/
├── scripts/
│   ├── login.py
│   ├── search_talent.py
│   ├── greet.py
│   ├── chat_followup.py
│   ├── run_pipeline.py
│   ├── export_seo_competency_xlsx_to_md.py  # 可选：从 xlsx 重导素质项 md
│   ├── interview_plan_md_to_docx.py        # 面试方案 md → Word
│   └── pdf_resume_to_md.py                 # 简历 PDF → md（供 Agent 读）
└── docs/
    └── REVERSE_ENGINEERING.md
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