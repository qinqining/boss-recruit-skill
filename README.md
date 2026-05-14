# Boss Recruit（招聘方技能仓库）

面向 **BOSS 直聘招聘方** 的自动化与配套 **离线 HR 文档任务**：浏览器内筛选、打招呼、沟通跟进；本地简历评分与面试方案（十问 + Word）生成。

> 给 **Claude Code / Cursor Agent** 的入口说明见根目录 **`SKILL.md`**、**`CLAUDE.md`**；BOSS 自动化话术与安全边界见 **`AGENTS.md`**。

---

## 功能概览

| 模块 | 说明 | 入口 |
|------|------|------|
| **登录** | Camoufox 持久化 profile，扫码保存 Cookie | `py boss login` / `py scripts/login.py` |
| **搜索牛人** | 进入推荐牛人、设置筛选 | `py boss search` |
| **打招呼** | 规则引擎读在线简历，匹配则打招呼，报告进 `reports/` | `py boss greet` / `py boss greet --top N` |
| **沟通跟进** | 沟通列表短跟进、索要简历、DOM 调试等 | `py boss followup`（详见 `scripts/chat_followup.py` 与 `AGENTS.md`） |
| **一键流水线** | 关键词 + 人数等 | `py boss run --keywords SEO --top 5` |
| **简历筛选评分（离线）** | 无浏览器；打分与归档 | `简历筛选评分任务/简历筛选任务.md` |
| **面试方案生成（离线）** | 十问 + `.docx` 交付；用户确认定稿后简历移入 `已处理简历/` | `面试方案生成任务/任务.md` |

---

## 环境要求

```bash
pip install "camoufox[geoip]" && camoufox fetch
```

- **Windows** 建议统一用 **`py`** 启动脚本（见各文档说明）。
- 离线任务常用：`pip install pymupdf python-docx`（PDF 转 md、面试方案 md → Word）。

---

## 常用命令（技能根目录）

```bash
py boss login
py boss search
py boss greet
py boss greet --top 15
py boss followup
py boss followup --dry-run --max 3
py boss run --keywords SEO --top 5
```

等价脚本路径：`scripts/login.py`、`greet.py`、`chat_followup.py` 等。

---

## 目录结构（摘要）

```
boss-recruit/
├── README.md                 # 本文件
├── SKILL.md                  # Cursor / Claude Skill 元数据与完整说明
├── CLAUDE.md                 # Claude Code Agent 精简指令
├── AGENTS.md                 # 跟进话术、风控与安全 Code
├── boss                      # Windows 下可用 py boss … 封装
├── scripts/                  # Python 自动化与工具脚本
├── 简历筛选评分任务/         # 离线：需筛选简历、案例、输出、已归档
├── 面试方案生成任务/         # 离线：岗位素质项、候选人简历、输出、已处理简历
├── reports/                  # 运行报告、跟进状态等（部分默认 .gitignore）
└── recruit_profile/          # Camoufox 用户数据目录（默认不入库）
```

---

## 文档怎么读

1. **第一次接项目**：`SKILL.md`（何时用、CLI、离线任务、文件树）。
2. **在 Claude Code 里执行任务**：`CLAUDE.md` + 各子目录 **`任务.md`**。
3. **沟通跟进与安全**：`AGENTS.md` + `scripts/chat_followup.py` 顶部说明。

子任务细节：

- 简历筛选：`简历筛选评分任务/README.md`、`简历筛选评分任务/简历筛选任务.md`
- 面试方案：`面试方案生成任务/任务.md`、`面试方案生成任务/岗位层级说明.md`

---

## 隐私与版本控制

候选人原文、评分产出、面试方案 Word、已处理简历等 **默认在 `.gitignore` 中忽略**；仓库内仅保留 `.gitkeep` 与任务说明。**勿将 Cookie、密钥、真实简历提交到公开仓库。**

---

## 维护说明

本仓库多为 **团队 / 个人技能与脚本**；对外分发时请自行补充许可证与脱敏策略。