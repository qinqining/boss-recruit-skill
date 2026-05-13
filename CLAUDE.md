# Boss Recruit — Agent 指令

> 在 BOSS 直聘上自动筛选牛人、打招呼、智能回复；以及仓库内的**离线**简历筛选评分与面试方案生成。

## 何时使用

当用户提到以下关键词时激活：
- 招聘、招人、HR
- BOSS直聘招聘方、牛人
- 打招呼、筛选简历
- 自动回复、聊天自动化
- **简历筛选评分**、简历打分、候选人归档、`简历筛选评分任务`
- **面试方案**、面试十问、素质项、`面试方案生成任务`

## 前置条件

```bash
pip install "camoufox[geoip]" && camoufox fetch
```

文档与自动化命令统一使用 Windows **`py`** 启动器（避免仅安装 Python 但未将 `python`/`python3` 加入 PATH 时出现 `command not found`）。非 Windows 可改为 `python3` 等价调用。

## 工具

项目路径下有 `boss` CLI，支持：

```bash
# 登录
py scripts/login.py

# 搜索牛人（页数：默认 2）
py boss search 2

# 打招呼前筛选（核心功能）：规则判定 + 自动打招呼；默认凑满 20 个匹配项即停；报告 → reports/
py boss greet

# 自定义人数上限：py boss greet --top 30
# 等价：py scripts/greet.py [--no-report]；默认可由 BOSS_GREET_TOP 覆盖

# 沟通列表「继续沟通」智能跟进（与 greet 同 profile，推荐）
py boss followup
py boss followup --dry-run

# 一键运行
py scripts/run_pipeline.py --keywords SEO --top 5
```

## 工作流程

当用户说"帮我筛选牛人"或"自动打招呼"时：

1. **确认需求** — 关键词（SEO/Google等）、数量、是否自动回复
2. **登录** — `py scripts/login.py`（首次需要扫码）
3. **搜索筛选** — `py boss greet`（默认最多成功打招呼 20；可选 `--top N`；报告见 `reports/`）
4. **展示结果** — 列出已打招呼的牛人
5. **列表跟进** — `py boss followup`（可选，见 `AGENTS.md`）

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

---

## 离线任务：简历筛选评分

**无浏览器**。按 `简历筛选评分任务/简历筛选任务.md` 执行。候选人简历为 **PDF** 时，先 `py scripts/pdf_resume_to_md.py …` 转 `.md` 再分析（见 `SKILL.md`）。

| 项 | 路径 |
|----|------|
| 任务说明 | `简历筛选评分任务/简历筛选任务.md` |
| 岗位 JD | `简历筛选评分任务/技术SEO岗位要求.md`（可按岗替换） |
| 待评简历 | `简历筛选评分任务/需筛选简历/` |
| 产出 | `简历筛选评分任务/输出/`，文件名含日期 `YYYY-MM-DD` |
| 归档 | `简历筛选评分任务/已归档/YYYY-MM-DD/` |

## 离线任务：面试方案生成

**无浏览器**。先读 `面试方案生成任务/岗位层级说明.md`，再读 `面试方案生成任务/任务.md`。

| 项 | 路径 |
|----|------|
| 任务说明 | `面试方案生成任务/任务.md` |
| 助理向校准 | `面试方案生成任务/岗位层级说明.md` |
| 素质表（Markdown） | `面试方案生成任务/岗位素质项/01_岗位与JD门槛.md`、`02_素质分档与面试母题.md` |
| 候选人材料 | `面试方案生成任务/候选人简历/`；**PDF 先** `py scripts/pdf_resume_to_md.py …pdf` **转 `.md` 再分析**（`pip install pymupdf`）；扫描件 OCR |
| 产出 | `面试方案生成任务/输出/`：**必须**含带日期的 **`面试方案_*.docx`**（Word）；可先写 `.md` 再 `py scripts/interview_plan_md_to_docx.py <路径>.md`（`pip install python-docx`） |

## 频率限制

- 打招呼间隔：5秒
- 列表跟进发送间隔：见 `config.json` → `followup.pause_after_send_sec`
- 单次最大打招呼：20条

## 技术文档

- `SKILL.md` — 完整功能说明（含 BOSS 自动化与离线任务）
- `docs/REVERSE_ENGINEERING.md` — BOSS 反爬研究