"""
LangGraph 状态定义 — AI 知识库工作流的核心数据结构

所有节点共享同一个 KBState，通过 TypedDict 保证类型安全。
每个节点只修改自己负责的字段，实现职责隔离。

「报告式通信」原则：state 中流转的是各节点的结构化摘要
（分析结果、审核意见、token 用量），而不是未经处理的原始数据。
"""

from typing import TypedDict


class KBState(TypedDict):
    """知识库工作流的全局状态

    数据流向: plan → sources → analyses → review ─[pass]→ organize → END
                                            ↓
                                          revise → review（循环）
                                            ↓[>max]
                                          human_flag → END

    字段职责:
        plan                 — Planner 写入，下游 Collector/Organizer/Reviewer 读取
        sources              — Collector 写入，Analyzer 读取（原始数据只在此停留）
        analyses             — Analyzer 写入，Reviewer 审核、Reviser 修正
        articles             — Organizer 写入，工作流正常终点的唯一产出
        review_feedback      — Reviewer 写入，Reviser 据此决定如何修改
        review_passed        — Reviewer 写入，Graph 条件边据此路由
        iteration            — Graph 的 revise 边自增，用于循环计数
        needs_human_review   — HumanFlag 写入，循环超限时的人工介入标记
        cost_tracker         — ModelClient 累加，所有 LLM 调用的 token 记账
    """

    plan: dict
    """Planner 节点输出的执行策略（报告式摘要，非原始配置）

    格式: {
        "strategy": str,             # 采集策略名，如 "conservative" / "balanced" / "aggressive"
        "per_source_limit": int,     # 每个数据源的最大采集条数
        "relevance_threshold": float,# 相关度过滤阈值（0~1），Organizer 用它筛低质条目
        "max_iterations": int,       # 审核循环的最大迭代次数（默认 3）
        "rationale": str             # 策略选择的理由（中文）
    }
    """

    sources: list[dict]
    """采集到的原始数据（Collector 写入，Analyzer 只读）

    每个元素是一条原始记录，格式: {
        "id": str,            # 唯一标识，如 "github-trending:owner/repo"
        "source": str,        # 数据来源，如 "github-trending"
        "title": str,         # 项目名 / 标题
        "url": str,           # 原始链接
        "collected_at": str,  # 采集时间，ISO 8601
        "stars": int,         # GitHub stars 数
        "description": str    # 原始描述文本
    }
    注意: 这是工作流中唯一存放原始数据的字段，下游只产出摘要。
    """

    analyses: list[dict]
    """LLM 分析后的结构化结果（Analyzer 写入，Reviewer 审核、Reviser 修正）

    每个元素是对一条 source 的分析摘要，格式: {
        "id": str,            # 与 sources 中对应记录的 id 一致，便于溯源
        "title": str,         # 规范化的中文标题
        "summary": str,       # 中文技术摘要
        "tags": list[str],    # 英文小写标签，如 ["ai", "agent"]
        "relevance_score": float,  # 相关度评分 0~1
        "category": str,      # 分类，如 "agent-framework"
        "key_insight": str    # 核心洞察（一句话，中文）
    }
    """

    articles: list[dict]
    """格式化、去重后的最终知识条目（Organizer 写入，工作流的产出）

    在审核通过后才产生，元素格式: {
        "id": str,            # 知识条目 ID，如 "2026-08-16-001"
        "title": str,
        "source": str,
        "url": str,
        "collected_at": str,  # ISO 8601
        "summary": str,
        "tags": list[str],
        "relevance_score": float,
        "category": str,
        "key_insight": str
    }
    """

    review_feedback: str
    """审核反馈意见（Reviewer 写入，Reviser 读取）

    中文自然语言描述，指出 analyses 的具体问题及修改建议。
    通过时可为空字符串；未通过时是 Reviser 修正的唯一依据。
    """

    review_passed: bool
    """审核是否通过（Reviewer 写入，Graph 条件边据此路由）

    True  → 进入 organize（正常产出）
    False → 进入 revise，按 review_feedback 修正后重新审核
    """

    iteration: int
    """当前审核循环次数（Graph 的 revise 边自增，0 起算）

    第 1 次审核时 iteration=0；每次未通过自增 1。
    达到 plan.max_iterations 时转 human_flag 兜底，防止无限循环。
    """

    needs_human_review: bool
    """是否需要人工介入（HumanFlag 写入，默认 False）

    审核循环超过 plan.max_iterations 仍未通过时置 True，
    工作流以 human_flag 终点结束，等待人工处理。
    """

    cost_tracker: dict
    """Token 用量追踪（ModelClient 累加，成本守卫的记账依据）

    格式: {
        "prompt_tokens": int,      # 累计输入 token 数
        "completion_tokens": int,  # 累计输出 token 数
        "total_cost_yuan": float   # 累计成本（人民币）
    }
    每次 LLM 调用后累加；超出预算时抛出 BudgetExceededError 中止工作流。
    """
