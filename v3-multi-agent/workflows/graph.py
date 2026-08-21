"""
LangGraph 工作流图定义 — V3 知识库（6 节点 + 3 路条件路由）

拓扑:

    ① collect → ② analyze → ③ organize → ④ review ┬─[pass]→ ③ organize → ⑥ save → END
                                                   ├─[fail, iter<3]→ ⑤ revise → ④ review（循环）
                                                   └─[fail, iter>=3]→ ⑦ human_flag → END

职责隔离（每个节点只做一件事）:
- collect    : 采集原始数据（不分析）
- analyze    : LLM 逐条生成摘要/标签/评分（不审核）
- organize   : 过滤低分 + URL 去重（不调 LLM、不修改内容）
- review     : 审核（当前为临时测试版，模拟循环；正式版在 reviewer.py）
- revise     : LLM 据反馈定向修改 analyses（只修改不评估）
- save       : 写盘 + 更新索引
- human_flag : 循环超限兜底，标记人工介入

关键决策点（3 路条件路由 route_after_review）:
- review_passed == True            → organize（再过滤一次后入库）
- review_passed == False, iter < 3  → revise（LLM 定向修改后回到 review）
- review_passed == False, iter >= 3 → human_flag（标记人工介入，异常终点）

organize 的条件出边 route_after_organize:
- review_passed == False（首次） → review（进入审核）
- review_passed == True（审核通过后） → save（写盘入库）
这样 organize 在审核前过滤一次、审核通过后再过滤一次，避免 pass→organize→review 死循环。

注意: 当前 review_node 用的是 nodes.py 里的临时测试版（前 3 次不通过、第 4 次起强制通过）。
测试版阈值已与 human_flag 阈值对齐：iteration 0/1/2 都不通过，第 3 次审核输出 iteration=3
时由 route_after_review 判 iter>=3 → human_flag 终止。因此测试可触发 human_flag 分支。
若要测试 pass→organize 分支，需把测试版的强制通过阈值调低（如改回 iter>=2）。
"""

from langgraph.graph import END, StateGraph

from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    review_node,  # 临时测试版（前 2 次不通过、第 3 次强制通过）
    save_node,
)
from workflows.reviser import revise_node
from workflows.human_flag import human_flag_node
from workflows.planner import planner_node
from workflows.state import KBState


def route_after_review(state: KBState) -> str:
    """3 路条件路由：review_node 之后的三个分支

    读 state["plan"]["max_iterations"]（默认 3）决定 human_flag 阈值，不再硬编码。

    - "organize"   → 审核通过，再过滤一次后入库
    - "revise"     → 审核未通过但 iteration < max，LLM 定向修改后回到 review
    - "human_flag" → 审核未通过且 iteration >= max，标记人工介入（异常终点）
    """
    plan = state.get("plan", {}) or {}
    max_iter = int(plan.get("max_iterations", 3))
    iteration = state.get("iteration", 0)

    if state.get("review_passed", False):
        return "organize"
    if iteration >= max_iter:
        print(f"[Router] iteration={iteration} >= max_iterations={max_iter}，转人工介入")
        return "human_flag"
    return "revise"


def route_after_organize(state: KBState) -> str:
    """organize 的条件出边：避免 pass→organize→review 死循环

    - review_passed == False（首次进入） → review（进入审核）
    - review_passed == True（审核通过后再过滤） → save（写盘入库）
    """
    if state.get("review_passed", False):
        return "save"
    return "review"


def build_graph() -> "CompiledGraph":
    """构建知识库工作流图（6 节点 + 3 路条件路由）并编译

    Returns:
        编译后的 LangGraph 应用，可直接 .invoke() / .stream() 执行
    """
    graph = StateGraph(KBState)

    # 【新增】注册 plan 节点
    graph.add_node("plan", planner_node)
    # --- 注册 6 个节点 ---
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("save", save_node)
    graph.add_node("human_flag", human_flag_node)

    # 【新增】plan → collect 边
    graph.add_edge("plan", "collect")
    # --- 线性边: collect → analyze → organize ---
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")

    # --- organize 的条件出边: 首次→review / 审核通过后→save ---
    graph.add_conditional_edges(
        "organize",
        route_after_organize,
        {
            "review": "review",
            "save": "save",
        },
    )

    # --- 【关键】3 路条件边: review → {organize, revise, human_flag} ---
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "organize": "organize",
            "revise": "revise",
            "human_flag": "human_flag",
        },
    )

    # --- revise 修改后回到 review（形成 Review Loop） ---
    graph.add_edge("revise", "review")

    # --- 两个终点 ---
    graph.add_edge("save", END)
    graph.add_edge("human_flag", END)

    # 【修改】入口从 "collect" 改为 "plan"
    graph.set_entry_point("plan")

    # 编译后返回，调用方拿到即可直接 .invoke() / .stream()
    return graph.compile()


# --- 暴露 app 供外部直接 import 使用 ---
app = build_graph()


# --- 便捷运行入口 ---
if __name__ == "__main__":
    print("=" * 60)
    print("AI 知识库 V3 — LangGraph 工作流启动（6 节点 + 3 路条件路由）")
    print("=" * 60)

    initial_state: KBState = {
        "plan": {},
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "needs_human_review": False,
        "cost_tracker": {},
    }

    for event in app.stream(initial_state):
        node_name = list(event.keys())[0]
        print(f"\n--- [{node_name}] 完成 ---")

        node_output = event[node_name]
        if "sources" in node_output:
            print(f"  采集数量: {len(node_output['sources'])}")
        if "analyses" in node_output:
            print(f"  分析数量: {len(node_output['analyses'])}")
        if "articles" in node_output:
            print(f"  入库数量: {len(node_output['articles'])}")
        if "review_passed" in node_output:
            passed = "通过" if node_output["review_passed"] else "未通过"
            print(f"  审核结果: {passed}")
            print(f"  迭代次数: {node_output.get('iteration', '?')}")
        if "needs_human_review" in node_output and node_output["needs_human_review"]:
            print(f"  ⚠️ 需要人工介入")
        if "cost_tracker" in node_output:
            cost = node_output["cost_tracker"].get("total_cost_yuan", 0)
            print(f"  累计成本: ¥{cost}")

    print("\n" + "=" * 60)
    print("工作流执行完毕")
    print("=" * 60)
