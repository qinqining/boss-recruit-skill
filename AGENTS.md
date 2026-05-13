# Boss Recruit — Skill Instructions（招聘方）

> 在 BOSS 直聘**招聘方**侧：推荐牛人筛选 → 打招呼 → **沟通列表「继续沟通」智能跟进** →（可选）消息监控。

本文件结构与求职端 `boss-auto-job-skills/AGENTS.md` 对齐，便于 Agent 统一理解「何时用哪条命令、文案与安全边界」。

## 何时使用

当用户提到以下关键词时激活本技能（`boss-recruit-skill`）：

- 招聘方、招人、HR、筛选牛人、推荐牛人  
- 打招呼、自动打招呼、简历规则匹配  
- **发消息后跟进**、**继续沟通**、**聊天跟进**、索要简历、约面试前闲聊  
- 智能回复、沟通列表、候选人跟进  

## 前置条件

```bash
pip install "camoufox[geoip]" && camoufox fetch
```

- 与 `greet` 相同：使用 **`recruit_profile`** 持久化浏览器目录（与 `scripts/login.py` / `scripts/greet.py` 共用），扫码一次后尽量同一环境复用。  
- 跟进文案里的**公司地址/通勤说明**写在 `config.json` 的 `followup` 段（见下），或环境变量 `BOSS_FOLLOWUP_LOCATION` / `BOSS_FOLLOWUP_COMMUTE_NOTE`。

## 工具（`boss` CLI，在技能根目录执行）

```bash
# 登录（首次扫码；Cookie/profile 写入 recruit_profile + ~/.agent-browser/auth/）
python scripts/login.py

# 推荐牛人：规则筛选 + 自动打招呼（默认最多 20 次成功打招呼，可 --top）
python boss greet
python boss greet --top 15 --no-report

# ★ 发消息后的列表跟进：打开沟通页，筛「继续沟通」，轮换短问（地址/经历/要简历）
python boss followup
python boss followup --max 3
python boss followup --dry-run

# 旧版消息轮询（async + 读 JSON cookie，与 greet 的 profile 不一定一致，仅兼容保留）
python boss monitor --interval 30
```

### 沟通页 URL

- 默认：`https://www.zhipin.com/web/boss/chat`（招聘方）  
- 若你账号实际入口不同：`BOSS_RECRUIT_CHAT_URL=... python scripts/chat_followup.py`

## 工作流程（推荐顺序）

当用户说「打完招呼后要跟进候选人」时：

1. **确认已用同一 profile 登录过** — 与打招呼同一台机、同一 `recruit_profile` 目录。  
2. **配置跟进话术变量** — 编辑 `config.json` → `followup.company_location`、`commute_note`（或环境变量）。  
3. **先 dry-run** — `python boss followup --dry-run --max 3`，确认控制台里**将要发送**的文案得体。  
4. **用户确认后再真实发送** — `python boss followup --max 5`（默认 5）。  
5. **不要高频连跑** — 与打招呼共用风控；单日对同一会话默认最多跟进 **2 轮**（`BOSS_FOLLOWUP_MAX_PER_DAY` 可调）。

## 智能跟进规则（脚本内置，非 LLM 也可运行）

脚本 `scripts/chat_followup.py` 对**含「继续沟通」的列表项**依次：

1. 点击打开会话，抓取当前页部分正文作上下文。  
2. 按**轮次**轮换三条主线（可在 `build_message` 中扩展）：  
   - **通勤/地址**：如「我们公司办公在『壹方天地B区』…您通勤可以接受吗？」（文案来自配置）  
   - **经历追问**：从聊天/简历摘要里猜一个**主题词**（如 SEO、独立站、投放…），问「最近一段主要负责哪块」  
   - **索要简历**：「方便发一份最新简历（PDF）吗…」  
3. 发送间隔默认 **6 秒**（`followup.pause_after_send_sec`），避免触发风控。

若你希望 **LLM 生成更活的开场**，可在后续迭代中接入与 `greet.py` 相同的 MiniMax/OpenAI 兼容接口；当前版本以**可配置 + 稳定 DOM**为主。

## 安全规则（必须遵守）

与求职端一致，遇到 BOSS 接口/页面风控码：

| 情况 | 行动 |
|------|------|
| Code 0 | 正常处理 |
| Code 37 | 按项目既有逻辑处理 zp_stoken（若有） |
| Code 36 | **立即停止**，告知用户到浏览器完成验证后再继续 |
| Code 32 | **立即停止**，告知用户手动发一条消息后再继续 |

**绝不重试 Code 36/32。**

## 频率限制（建议）

- 单次跟进：`--max` 建议 **3～5**，默认 5。  
- 每条发送后 **≥6s** 再处理下一人（可调 `pause_after_send_sec`）。  
- 同一候选人**自然日内**脚本默认最多 **2 条**跟进（防骚扰）；状态文件：`reports/followup_state.json`。  
- 与「打招呼」合计：**宁少勿多**，账号异常时先停所有自动化。

## 技术文档

- `SKILL.md` — 技能总览与步骤说明  
- `docs/REVERSE_ENGINEERING.md` — 风控相关笔记  
- `scripts/greet.py` — 打招呼与简历规则  
- `scripts/chat_followup.py` — **继续沟通列表跟进**实现  
