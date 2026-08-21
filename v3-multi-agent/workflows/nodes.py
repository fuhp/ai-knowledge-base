"""
workflows/nodes.py — LangGraph 工作流节点（collect/analyze/organize/save）

拓扑（完整图定义见 workflows/graph.py）:
    collect → analyze → organize → review ┬─[pass]→ save → END
                                         └─[fail]→ revise → review（循环）

设计要点:
- 每个节点是纯函数：接收 KBState，返回 dict（部分状态更新）
- organize 只做过滤低分 + URL 去重，不调 LLM、不修改内容
- review_node 在 workflows/reviewer.py（5 维度加权评分，代码重算总分）
- revise_node 在 workflows/reviser.py（据 feedback 用 LLM 定向修改 analyses）
- save 写 knowledge/articles/*.json 并更新 index.json

注意: review_node / revise_node 不在本文件，分别在 reviewer.py / reviser.py。
本文件保留 review_node_test 仅供循环路由测试（graph.py 正式运行用 reviewer.py 的版本）。
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState


# ---------------------------------------------------------------------------
# ① collect_node — GitHub Search API 采集 AI 相关仓库
# ---------------------------------------------------------------------------
def collect_node(state: KBState) -> dict:
    """采集节点：urllib.request 调 GitHub Search API，抓 AI 相关热门仓库。

    只采集不分析。读取 plan.per_source_limit 决定抓取条数（默认 10）。
    网络失败不中断流程，记一条 [ERROR] 占位条目。
    """
    print("[Collect] 开始采集 GitHub AI 相关仓库")

    plan = state.get("plan", {}) or {}
    per_source_limit = int(plan.get("per_source_limit", 10))

    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    one_week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    query = f"ai agent llm stars:>100 pushed:>{one_week_ago}"
    url = (
        f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}"
        f"&sort=stars&per_page={per_source_limit}"
    )

    sources: list[dict] = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for repo in data.get("items", []):
            sources.append({
                "id": f"github:{repo['full_name']}",
                "source": "github",
                "title": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description", "") or "",
                "stars": repo.get("stargazers_count", 0),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
        print(f"[Collect] 采集到 {len(sources)} 条")
    except Exception as e:
        print(f"[Collect] 采集失败: {e}")
        sources.append({
            "id": "github:error",
            "source": "github",
            "title": "[ERROR] GitHub API 请求失败",
            "url": "",
            "description": str(e),
            "stars": 0,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        })

    return {"sources": sources}


# ---------------------------------------------------------------------------
# ② analyze_node — LLM 逐条生成中文摘要/标签/评分
# ---------------------------------------------------------------------------
def analyze_node(state: KBState) -> dict:
    """分析节点：对每条 source 调 LLM 生成 summary/tags/relevance_score/category/key_insight。

    一次一条，不做跨条目聚合。跳过 [ERROR] 占位条目。
    单条分析失败不中断，记一条 relevance_score=0 的占位。
    """
    print("[Analyze] 开始逐条 LLM 分析")

    sources = state.get("sources", [])
    tracker = state.get("cost_tracker", {})
    analyses: list[dict] = []

    for item in sources:
        if item.get("title", "").startswith("[ERROR]"):
            continue

        prompt = f"""请分析以下技术项目，用 JSON 返回：

项目名: {item['title']}
描述: {item.get('description', '无描述')}
URL: {item.get('url', '')}

返回 JSON:
{{
  "summary": "200字以内中文技术摘要",
  "tags": ["tag1", "tag2"],
  "relevance_score": 0.8,
  "category": "agent|llm|rag|tool|framework|other",
  "key_insight": "一句话核心洞察"
}}"""
        try:
            result, usage = chat_json(prompt)
            tracker = accumulate_usage(tracker, usage)
            analyses.append({
                **item,
                "summary": result.get("summary", ""),
                "tags": result.get("tags", []),
                "relevance_score": float(result.get("relevance_score", 0.5)),
                "category": result.get("category", "other"),
                "key_insight": result.get("key_insight", ""),
            })
        except Exception as e:
            print(f"[Analyze] 分析失败 {item['title']}: {e}")
            analyses.append({
                **item,
                "summary": f"分析失败: {e}",
                "tags": [],
                "relevance_score": 0.0,
                "category": "error",
                "key_insight": "",
            })

    print(f"[Analyze] 完成 {len(analyses)} 条")
    return {"analyses": analyses, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# ③ organize_node — 过滤低分 + URL 去重（纯整理，不做 LLM 修正）
# ---------------------------------------------------------------------------
# LLM 定向修正已移至独立的 revise_node（workflows/reviser.py），
# 遵循"职责隔离"原则：organize 只整理，revise 只修改，review 只评分。
def organize_node(state: KBState) -> dict:
    """整理节点：过滤低分(<0.6) + URL 去重。

    纯整理节点，不调用 LLM。审核未通过时的 LLM 定向修正由独立的
    revise_node 负责（review → revise → review 循环）。
    """
    print("[Organize] 开始整理（过滤/去重）")

    analyses = state.get("analyses", [])

    # Step 1: 过滤低分（< 0.6）
    threshold = 0.6
    qualified = [a for a in analyses if a.get("relevance_score", 0) >= threshold]

    # Step 2: URL 去重
    seen: set[str] = set()
    unique: list[dict] = []
    for item in qualified:
        u = item.get("url", "")
        if u and u in seen:
            continue
        if u:
            seen.add(u)
        unique.append(item)

    print(f"[Organize] 过滤+去重后剩 {len(unique)} 条")
    return {"analyses": unique}


# ---------------------------------------------------------------------------
# ④ review_node — 【临时测试版】模拟审核循环：前 3 次不通过，第 4 次起强制通过
# ---------------------------------------------------------------------------
# ⚠️ 临时测试代码：用于验证 3 路条件路由（organize / revise / human_flag）。
# 阈值对齐 graph.py 的 route_after_review：iteration >= 3 → human_flag。
# 因此 iteration 0/1/2 都不通过，第 3 次审核（输出 iteration=3）由路由判 human_flag 终止。
# iteration >= 3 的强制通过仅作防御性兜底（正常情况下路由会先终止）。
def review_node(state: KBState) -> dict:
    """审核节点（临时测试版）：模拟审核循环，不调用 LLM。

    行为（阈值与 graph.py 的 human_flag 阈值 >=3 对齐）:
    - iteration 0 (第 1 次审核) → review_passed=False，feedback 指出摘要质量问题
    - iteration 1 (第 2 次审核) → review_passed=False，feedback 指出标签问题
    - iteration 2 (第 3 次审核) → review_passed=False，feedback 指出分类与一致性问题
      （此时输出 iteration=3，route_after_review 判 iter>=3 → human_flag 终止）
    - iteration >= 3 → review_passed=True，强制通过（防御性兜底，正常不可达）
    每次打印当前 iteration 和 review_passed 值。
    """
    print("[Review] 开始审核（临时测试模式，不调 LLM）")

    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})

    # 防御性兜底：iteration >= 3 强制通过（正常由路由在 iter>=3 时转 human_flag，不会到此）
    if iteration >= 3:
        passed = True
        feedback = "第4次及以后审核：强制通过（测试模式兜底）"
    else:
        passed = False
        if iteration == 0:
            feedback = (
                "第1次审核反馈：摘要质量不足，部分条目 summary 过短（<100字），"
                "缺少技术原理与实现细节，请扩充并补充关键洞察。"
            )
        elif iteration == 1:
            feedback = (
                "第2次审核反馈：标签准确性仍有问题，存在重复标签、"
                "大小写不一致（应英文小写）、部分条目 category 与项目主题不匹配，"
                "请规范化 tags 并修正 category。"
            )
        else:  # iteration == 2
            feedback = (
                "第3次审核反馈：分类与一致性仍有问题，部分条目 category 归类不当、"
                "summary 与 tags 语义不一致、key_insight 偏空泛，"
                "请对齐分类并强化洞察。"
            )

    print(f"[Review] iteration={iteration}, review_passed={passed}")
    print(f"[Review] feedback={feedback}")

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }


# ---------------------------------------------------------------------------
# ⑤ save_node — 写入 knowledge/articles/*.json + 更新 index.json
# ---------------------------------------------------------------------------
def save_node(state: KBState) -> dict:
    """保存节点：把通过审核的 analyses 格式化为 articles 写盘，并更新 index.json。

    ID 格式: {YYYY-MM-DD}-{NNN}。index.json 追加去重，不覆盖已有条目。
    """
    print("[Save] 开始写盘")

    analyses = state.get("analyses", [])
    tracker = state.get("cost_tracker", {})
    if not analyses:
        print("[Save] 无条目可保存")
        return {"articles": []}

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    articles_dir = os.path.join(base_dir, "knowledge", "articles")
    os.makedirs(articles_dir, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    articles: list[dict] = []
    for i, item in enumerate(analyses):
        articles.append({
            "id": f"{today}-{i:03d}",
            "title": item.get("title", ""),
            "source": item.get("source", "unknown"),
            "url": item.get("url", ""),
            "collected_at": item.get("collected_at", ""),
            "summary": item.get("summary", ""),
            "tags": item.get("tags", []),
            "relevance_score": item.get("relevance_score", 0.5),
            "category": item.get("category", "other"),
            "key_insight": item.get("key_insight", ""),
        })

    # 写每条 article
    for article in articles:
        path = os.path.join(articles_dir, f"{article['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False, indent=2)

    # 更新 index.json（追加，去重）
    index_path = os.path.join(articles_dir, "index.json")
    index: list[dict] = []
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            index = []

    existing_ids = {e.get("id") for e in index}
    for a in articles:
        if a["id"] not in existing_ids:
            index.append({
                "id": a["id"],
                "title": a["title"],
                "category": a.get("category", "other"),
                "relevance_score": a.get("relevance_score", 0.5),
            })
            existing_ids.add(a["id"])

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"[Save] 写入 {len(articles)} 篇，索引共 {len(index)} 条")
    return {"articles": articles, "cost_tracker": tracker}
