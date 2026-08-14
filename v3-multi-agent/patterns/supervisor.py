"""
Supervisor 模式 — 主管监督 + 审核循环

Supervisor 模式的核心思想:
1. Worker Agent 执行任务，产出 JSON 格式的分析报告
2. Supervisor Agent 从三个维度审核报告质量（准确性/深度/格式）
3. 审核不通过则带反馈重做，最多 N 轮；超限强制返回 + 警告

与 Router 模式的区别:
- Router: 1对1 分发（一个请求 → 一个处理器）
- Supervisor: 监督循环（产出 → 审核 → 反馈 → 重做，质量门控）
"""

import json
import re
from typing import Any

from workflows.model_client import chat


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def parse_json(text: str) -> dict | list:
    """容错解析 LLM 返回的 JSON（chat() 返回纯文本，需自行解析）

    容错策略:
    1. 去掉 markdown 代码块包裹
    2. 直接 json.loads
    3. 正则提取第一个 {...} 或 [...] 结构
    4. 全部失败返回 {}
    """
    cleaned = text.strip()

    # 策略 1: 去掉 ```json ... ``` 包裹
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        cleaned = "\n".join(lines[start:end])

    # 策略 2: 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 策略 3: 正则提取第一个完整 JSON 结构
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    return {}


# ---------------------------------------------------------------------------
# Worker / Supervisor Agent 定义
# ---------------------------------------------------------------------------

def worker_agent(task: str) -> dict:
    """Worker Agent：接收任务，输出 JSON 格式的分析报告

    Args:
        task: 任务描述（可能附带上一轮审核反馈）

    Returns:
        JSON 报告 {"report": ..., "key_points": [...]}
    """
    prompt = f"""请完成以下任务，输出 JSON 格式的分析报告。

任务: {task}

请返回 JSON 格式:
{{
    "report": "分析报告正文（包含分析过程和结论）",
    "key_points": ["要点1", "要点2", "要点3"]
}}"""

    result, _ = chat(prompt, system="你是分析 Worker。只返回 JSON，不要输出其他内容。")
    parsed = parse_json(result)
    return parsed if isinstance(parsed, dict) else {"report": result}


def supervisor_agent(report: dict, task: str) -> dict:
    """Supervisor Agent：对 Worker 输出进行质量审核

    评分维度（各 1-10 分）:
    - accuracy: 准确性
    - depth: 深度
    - format: 格式

    Args:
        report: Worker 产出的分析报告
        task: 原始任务描述

    Returns:
        {"passed": bool, "score": int, "feedback": str, ...维度分}
    """
    prompt = f"""请审核以下针对任务的分析报告。

原始任务: {task}

分析报告:
{json.dumps(report, ensure_ascii=False, indent=2)}

请从三个维度打分（各 1-10 分）:
- accuracy: 准确性 — 内容是否正确、有依据
- depth: 深度 — 分析是否深入、有洞察
- format: 格式 — 结构是否清晰、表达是否规范

返回 JSON 格式:
{{
    "accuracy": 8,
    "depth": 7,
    "format": 9,
    "score": 8,
    "passed": true,
    "feedback": "改进建议（未通过时必填）"
}}

判定标准: 综合分 score >= 7 为通过（passed=true），否则为不通过。"""

    try:
        result, _ = chat(
            prompt,
            system="你是质量审核主管。只返回 JSON，不要输出其他内容。",
            max_tokens=500,
        )
        review = parse_json(result)
    except Exception as e:
        # 审核失败时默认通过，保证可用性
        return {"passed": True, "score": 7, "feedback": f"审核失败({e})，默认通过"}

    if not isinstance(review, dict):
        return {"passed": True, "score": 7, "feedback": "审核输出无法解析，默认通过"}

    # 标准化: 保证关键字段存在，score 取整
    score = int(review.get("score", 0))
    review["score"] = score
    review.setdefault("passed", score >= 7)
    review.setdefault("feedback", "")
    return review


# ---------------------------------------------------------------------------
# Supervisor 核心 — 审核循环
# ---------------------------------------------------------------------------

def supervisor(task: str, max_retries: int = 3) -> dict:
    """主管入口：驱动 Worker 产出 + Supervisor 审核的循环

    审核循环规则:
    - 通过（score >= 7）→ 返回结果
    - 不通过 → 带反馈重做，最多 max_retries 轮
    - 超过 max_retries 轮 → 强制返回 + 警告

    Args:
        task: 用户任务描述
        max_retries: 最大重试轮数（默认 3）

    Returns:
        {
            "output": Worker 的最终报告,
            "attempts": 实际执行轮数,
            "final_score": 最后一轮综合分,
            "warning": 超过重试上限时的警告（仅超限时存在）
        }
    """
    feedback = ""
    report: dict = {}

    for attempt in range(1, max_retries + 1):
        print(f"[Supervisor] 第 {attempt} 轮: Worker 执行")

        # 带上一轮反馈重做（第一轮无反馈）
        work_task = task if not feedback else f"{task}\n\n上一轮审核反馈（请针对性修正）:\n{feedback}"
        report = worker_agent(work_task)

        print(f"[Supervisor] 第 {attempt} 轮: Supervisor 审核")
        review = supervisor_agent(report, task)
        final_score: int = review["score"]
        print(f"[Supervisor] 第 {attempt} 轮: 评分 {final_score}/10 {'通过' if final_score >= 7 else '不通过'}")

        # 通过 → 直接返回
        if final_score >= 7:
            return {
                "output": report,
                "attempts": attempt,
                "final_score": final_score,
            }

        feedback = review.get("feedback") or "请提升分析深度与准确性。"

    # 超过重试上限 → 强制返回 + 警告
    warning = f"已重试 {max_retries} 轮仍未达标，强制返回最后一轮结果"
    print(f"[Supervisor] {warning}")
    return {
        "output": report,
        "attempts": max_retries,
        "final_score": final_score,
        "warning": warning,
    }


# --- 命令行测试入口 ---
if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "分析 2026 年 AI Agent 框架的发展趋势"
    print(f"任务: {task}\n")

    result = supervisor(task)

    print("\n" + "=" * 60)
    print(f"执行轮数: {result['attempts']}")
    print(f"最终评分: {result['final_score']}/10")
    if "warning" in result:
        print(f"警告: {result['warning']}")
    print("-" * 60)
    print("最终报告:")
    print(json.dumps(result["output"], ensure_ascii=False, indent=2))
