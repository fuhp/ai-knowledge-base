# AI Knowledge Base — AI 知识库

> 一个自动化技术情报收集与分析系统：持续追踪 GitHub Trending、Hacker News、arXiv 等来源，
> 通过多 Agent 协作将分散的技术资讯转化为结构化、可检索的 JSON 知识条目。

本项目以**四个递进版本**组织，每个版本在前一版基础上叠加一层能力，完整呈现一条
从「骨架」到「生产」的演进路径：

| 版本 | 目录 | 核心能力 | 关键新增 |
|------|------|---------|---------|
| **V1** | `v1-skeleton/` | Agent 角色定义 + 手动流水线 | collector / analyzer / organizer 三 Agent，OpenCode `@agent` 调用 |
| **V2** | `v2-automation/` | 自动化流水线 | Pipeline 脚本、Hooks 质量校验、GitHub Actions 定时采集 |
| **V3** | `v3-multi-agent/` | 多 Agent 工作流 | LangGraph 状态图、审核循环（Review Loop）、设计模式、成本守卫/安全/评估 |
| **V4** | `v4-production/` | 生产平台 | 多渠道分发（Telegram/飞书）、交互式机器人、OpenClaw 网关、Docker 容器化 |

## 目录结构

```
ai-knowledge-base/
├── README.md                # 本文件
├── .gitignore
├── .github/workflows/       # 根级 CI（daily-collect, daily-collect-v4）
│
├── v1-skeleton/             # 骨架：Agent 角色定义 + 手动三阶段流水线
│   ├── AGENTS.md
│   ├── .opencode/           # agents/ + skills/ 角色与技能定义
│   └── knowledge/           # raw/ 原始数据 + articles/ 知识条目
│
├── v2-automation/           # 自动化：Pipeline + Hooks + CI/CD
│   ├── AGENTS.md
│   ├── pipeline/            # pipeline.py 四步流水线 + model_client.py
│   ├── hooks/               # validate_json.py / check_quality.py 质量校验
│   ├── scripts/             # 运行脚本
│   └── knowledge/
│
├── v3-multi-agent/          # 多 Agent：LangGraph 工作流 + 审核循环
│   ├── AGENTS.md
│   ├── RUN.md               # 运行指南
│   ├── workflows/           # state / nodes / graph / planner / reviewer / reviser / human_flag
│   ├── patterns/           # router.py / supervisor.py 设计模式
│   ├── tests/               # cost_guard.py / security.py / eval_test.py
│   ├── pipeline/            # V2 兼容入口
│   └── knowledge/
│
└── v4-production/           # 生产：分发 + 机器人 + 容器化
    ├── AGENTS.md
    ├── RUN.md
    ├── Dockerfile
    ├── docker-compose.yml
    ├── distribution/        # formatter.py / publisher.py 多渠道分发
    ├── bot/                 # knowledge_bot.py 交互式机器人
    ├── openclaw/            # OpenClaw 网关配置 + 人格 + 技能
    ├── workflows/           # 继承自 V3
    ├── patterns/            # 继承自 V3
    ├── scripts/             # deploy.sh 部署脚本
    └── knowledge/
```

## 版本演进

### V1 · 骨架（`v1-skeleton/`）

定义三个 Agent 角色，通过 OpenCode 的 `@agent` 语法手动驱动三阶段流水线：

```
@collector 采集 → @analyzer 分析 → @organizer 整理 → knowledge/articles/*.json
```

- 单向数据流：Collector → Analyzer → Organizer
- 质量门控：评分低于 0.6 的条目被 Organizer 丢弃
- 输出标准化 JSON（`id` / `title` / `source` / `url` / `summary` / `tags` / `relevance_score`）

### V2 · 自动化（`v2-automation/`）

在 V1 骨架上加入自动化执行与质量校验：

- **Pipeline**：`python pipeline/pipeline.py --sources github,rss --limit 20` 一键跑通采集→分析→整理→保存
- **Hooks**：写入文章时自动校验 JSON 格式与五维质量评分（A/B/C 等级）
- **CI/CD**：GitHub Actions 每日 8:00 UTC 自动采集并提交

三种运行模式：手动 `@agent` / 自动 Pipeline / 定时 GitHub Actions。

### V3 · 多 Agent（`v3-multi-agent/`）

引入 LangGraph 状态图编排工作流，**审核循环是核心教学点**：

```
plan → collect → analyze → organize → review ┬─[pass]→ save → END
                                              ├─[fail, iter<max]→ revise → review（循环）
                                              └─[fail, iter>=max]→ human_flag → END
```

- **LangGraph 工作流**：`StateGraph(KBState)` + 条件边，节点为纯函数
- **审核循环**：Reviewer 5 维度加权评分（代码重算总分，不信任 LLM 算术），未通过由 Reviser 定向修正后重审，超限转人工
- **设计模式**：Router（意图路由 1:1）/ Supervisor（主管调度 1:N）
- **生产加固**：CostGuard 成本守卫（追踪/预警/熔断）、安全模块（防注入/PII/限流）、pytest 评估测试（含 LLM-as-Judge）

运行：

```bash
cd v3-multi-agent
pip install -r requirements.txt
cp .env.example .env   # 填 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
python -m workflows.graph          # 跑 LangGraph 工作流
pytest tests/eval_test.py -v        # 评估测试
python tests/cost_guard.py          # 成本守卫（零 LLM）
```

详见 `v3-multi-agent/RUN.md`。

### V4 · 生产（`v4-production/`）

在 V3 工作流基础上叠加分发、交互与部署能力（**不重写 V3，而是继承**）：

- **四阶段流水线**：采集 → 分析 → 整理 → **分发**
- **多渠道分发**：Telegram 频道、飞书群组（异步 `aiohttp` 发布）
- **交互式机器人**：意图识别、命令系统、权限分级（read/write/delete）
- **OpenClaw 网关**：统一接入，Bot 人格定义（SOUL.md）+ 知识检索技能
- **Docker 一键部署**：`Dockerfile` + `docker-compose.yml`

运行：

```bash
cd v4-production
pip install -r requirements.txt
cp .env.example .env
docker-compose up -d               # 容器化部署
python daily_digest.py             # 每日简报
```

详见 `v4-production/RUN.md`。

## 技术栈

- **运行时**：Python 3.11+（V4 用 3.12 + asyncio）
- **Agent 框架**：OpenCode（V1/V2 手动 `@agent`）
- **工作流引擎**：LangGraph（V3+，`StateGraph` + 条件边）
- **LLM**：DeepSeek / Qwen / OpenAI（OpenAI 兼容 API，按 `.env` 切换）
- **数据源**：GitHub API v3、Hacker News (Firebase)、arXiv
- **分发渠道**（V4）：Telegram Bot API、飞书 Webhook
- **容器化**（V4）：Docker + Docker Compose
- **测试**：pytest（含 `pytest.mark.slow` 标记 LLM 测试）
- **CI/CD**：GitHub Actions

## 编码规范

- **文件命名**：原始数据 `knowledge/raw/{source}-{YYYY-MM-DD}.json`；知识条目 `knowledge/articles/{YYYY-MM-DD}-{slug}.json`；索引 `knowledge/articles/index.json`
- **JSON**：2 空格缩进、UTF-8、ISO 8601 日期
- **必填字段**：`id` / `title` / `source` / `url` / `collected_at` / `summary` / `tags` / `relevance_score`
- **语言约定**：代码与 JSON 键名用英文；摘要/分析/注释用中文；标签用英文小写连字符（如 `large-language-model`）
- **Agent 协作**：单向数据流、职责隔离、幂等性、质量门控（< 0.6 丢弃）、可追溯

## 快速开始

推荐从 **V3** 入手理解工作流与审核循环，再用 **V4** 看生产化扩展：

```bash
# 1. 配置 LLM（任选一家 OpenAI 兼容服务）
cd v3-multi-agent
cp .env.example .env
# 编辑 .env：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

# 2. 安装依赖
pip install -r requirements.txt

# 3. 跑工作流
python -m workflows.graph

# 4. 跑测试（跳过需要 LLM 的慢测试）
pytest tests/eval_test.py -v -m "not slow"
```

## 许可证

见各版本目录下的说明。
