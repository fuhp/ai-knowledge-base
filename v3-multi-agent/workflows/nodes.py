"""
workflows/nodes.py — 5 节点 LangGraph 工作流（精简版）

拓扑:
    collect → analyze → organize → review ┬─[pass]→ save → END
                                          └─[fail]→ organize（带 feedback 修正，循环）

设计要点:
- 每个节点是纯函数：接收 KBState，返回 dict（部分状态更新）
- organize 在 iteration > 0 且有 feedback 时调 LLM 做定向修正
- review 四维度评分；iteration >= 2 强制通过，防止死循环
- save 写 knowledge/articles/*.json 并更新 index.json

注意: 同目录下还有一套 7 节点版本（planner/collector/analyzer/reviewer/
reviser/organizer/human_flag），是更完整的"职责隔离"实现。本文件是按需求
精简的 5 节点版本，与那套独立，互不引用。
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
# ③ organize_node — 过滤/去重，必要时按 feedback 用 LLM 修正
# ---------------------------------------------------------------------------
def organize_node(state: KBState) -> dict:
    """整理节点：过滤低分(<0.6) + URL 去重；iteration > 0 且有 feedback 时调 LLM 定向修正。

    第 1 轮（iteration=0）只做过滤+去重；后续轮次带上 review_feedback
    让 LLM 定向改 analyses 的弱项，再过滤+去重。
    """
    print("[Organize] 开始整理（过滤/去重" + ("+修正" if state.get("review_feedback") else "") + "）")

    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")
    tracker = state.get("cost_tracker", {})

    # Step 1: iteration > 0 且有 feedback → LLM 定向修正
    if iteration > 0 and feedback:
        prompt = f"""你是知识库编辑。根据审核反馈定向修改这些分析结果。

【审核反馈】
{feedback}

【当前分析结果】
{json.dumps(analyses, ensure_ascii=False, indent=2)}

要求:
- 重点改进反馈指出的弱项
- 保留已不错的部分，不要过度修改
- 保持字段结构不变
- 返回修改后的 JSON 数组（与输入同结构）"""
        try:
            revised, usage = chat_json(prompt, temperature=0.4)
            tracker = accumulate_usage(tracker, usage)
            if isinstance(revised, list) and revised:
                analyses = revised
                print(f"[Organize] LLM 修正 {len(analyses)} 条")
        except Exception as e:
            print(f"[Organize] LLM 修正失败，沿用原 analyses: {e}")

    # Step 2: 过滤低分（< 0.6）
    threshold = 0.6
    qualified = [a for a in analyses if a.get("relevance_score", 0) >= threshold]

    # Step 3: URL 去重
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
    return {"analyses": unique, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# ④ review_node — LLM 四维度评分，iteration >= 2 强制通过
# ---------------------------------------------------------------------------
def review_node(state: KBState) -> dict:
    """审核节点：四维度评分（摘要质量/标签准确/分类合理/一致性）。

    输出 JSON: {"passed": bool, "overall_score": float, "feedback": str, "scores": {...}}
    iteration >= 2 时强制通过，防止无限循环。
    LLM 调用失败时自动通过，不阻塞流程。
    """
    print("[Review] 开始审核")

    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})

    # 强制通过：达到上限 iteration
    if iteration >= 2:
        print(f"[Review] iteration={iteration} >= 2，强制通过")
        return {
            "review_passed": True,
            "review_feedback": "达到最大迭代次数，强制通过",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    if not analyses:
        return {
            "review_passed": True,
            "review_feedback": "无条目需要审核",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    sample = analyses[:5]
    prompt = f"""你是知识库质量审核员。请审核以下分析结果：

{json.dumps(sample, ensure_ascii=False, indent=2)}

按四个维度评分（每项 1-5 分）:
1. summary_quality  - 摘要质量（准确、简洁、有洞察）
2. tag_accuracy      - 标签准确（与内容匹配、英文小写）
3. category_fit      - 分类合理（category 与项目主题一致）
4. consistency       - 一致性（字段完整、各字段间无矛盾）

用 JSON 返回:
{{
  "passed": true,
  "overall_score": 4.2,
  "feedback": "具体改进建议，指出弱项",
  "scores": {{
    "summary_quality": 4,
    "tag_accuracy": 5,
    "category_fit": 4,
    "consistency": 4
  }}
}}

当前是第 {iteration + 1} 次审核。"""

    try:
        result, usage = chat_json(
            prompt,
            system="你是严格但公正的知识库审核员。给出具体可操作的反馈。",
            temperature=0.1,
        )
        tracker = accumulate_usage(tracker, usage)

        passed = bool(result.get("passed", False))
        overall = float(result.get("overall_score", 0))
        feedback = result.get("feedback", "")
        scores = result.get("scores", {})

        print(f"[Review] 总分 {overall}/5, 通过={passed}, 第 {iteration + 1} 次")
        print(f"[Review] 各维度: {scores}")
    except Exception as e:
        print(f"[Review] 审核失败，自动通过: {e}")
        passed = True
        feedback = f"审核 LLM 调用失败: {e}，自动通过"

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
