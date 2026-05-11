# Boss Recruit — Agent 指令

> 在 BOSS 直聘上自动筛选牛人、打招呼、智能回复。

## 何时使用

当用户提到以下关键词时激活：
- 招聘、招人、HR
- BOSS直聘招聘方、牛人
- 打招呼、筛选简历
- 自动回复、聊天自动化

## 前置条件

```bash
pip install "camoufox[geoip]" && camoufox fetch
```

## 工具

项目路径下有 `boss` CLI，支持：

```bash
# 登录
python scripts/login.py

# 搜索牛人（页数：默认 2）
python boss search 2

# 打招呼前筛选（核心功能）：规则判定 + 自动打招呼；默认凑满 20 个匹配项即停；报告 → reports/
python boss greet

# 自定义人数上限：python boss greet --top 30
# 等价：python scripts/greet.py [--no-report]；默认可由 BOSS_GREET_TOP 覆盖

# 消息监控 + 智能回复
python scripts/chat_auto.py --interval 30

# 一键运行
python scripts/run_pipeline.py --keywords SEO --top 5
```

## 工作流程

当用户说"帮我筛选牛人"或"自动打招呼"时：

1. **确认需求** — 关键词（SEO/Google等）、数量、是否自动回复
2. **登录** — `python scripts/login.py`（首次需要扫码）
3. **搜索筛选** — `python boss greet`（默认最多成功打招呼 20；可选 `--top N`；报告见 `reports/`）
4. **展示结果** — 列出已打招呼的牛人
5. **监控回复** — `python scripts/chat_auto.py`（可选）

## 打招呼前筛选逻辑

Agent 读取牛人在线简历，判断是否包含关键词：
- SEO、搜索引擎优化
- Google、AdWords
- SEM、数据分析
- GA、Google Analytics

**匹配才打招呼，不匹配跳过。**

## 智能回复判断

Agent 分析候选人回复，决定：
- **invite**：发送面试邀请（强意向：问薪资/时间/面试）
- **continue**：继续跟进（有疑问需要解答）
- **pass**：标记不合适（消极/拒绝）

## 安全规则

| 情况 | 行动 |
|------|------|
| Code 0 | 正常处理 |
| Code 37 | 自动生成zp_stoken重试 |
| Code 36 | **立即停止**，用户验证 |
| Code 32 | **立即停止**，用户手动发消息 |

**绝不重试 Code 36/32。**

## 频率限制

- 打招呼间隔：5秒
- 消息检查间隔：30秒
- 单次最大：20条

## 技术文档

- `SKILL.md` — 完整功能说明
- `docs/REVERSE_ENGINEERING.md` — BOSS 反爬研究