"""
LangGraph 工作流图定义 — V3 知识库（5 节点精简版）

拓扑:

    ① collect → ② analyze → ③ organize → ④ review ┬─[pass]→ ⑤ save → END
                                                   └─[fail]→ ③ organize（带 feedback 修正，循环）

关键决策点（条件路由）:
- review_passed == True  → 路由到 save（写盘入库，正常终点）
- review_passed == False → 路由回 organize（带 review_feedback 让 LLM 定向修正后重审）

防死循环: review_node 在 iteration >= 2 时强制通过，因此最多 3 次审核。
"""

from langgraph.graph import END, StateGraph

from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    review_node,
    save_node,
)
from workflows.state import KBState


def route_after_review(state: KBState) -> str:
    """条件路由：review_node 之后的两条分支

    - "save"     → 审核通过，写盘入库（正常终点）
    - "organize" → 审核未通过，带 feedback 回到 organize 修正后重审
    """
    if state.get("review_passed", False):
        return "save"
    return "organize"


def build_graph() -> "CompiledGraph":
    """构建知识库工作流图（5 节点精简版）并编译

    Returns:
        编译后的 LangGraph 应用，可直接 .invoke() / .stream() 执行
    """
    graph = StateGraph(KBState)

    # --- 注册 5 个节点 ---
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("save", save_node)

    # --- 线性边: collect → analyze → organize → review ---
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    # --- 【关键】二路条件边: review → {save, organize} ---
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "save": "save",
            "organize": "organize",
        },
    )

    # --- 终点 ---
    graph.add_edge("save", END)

    # --- 入口 ---
    graph.set_entry_point("collect")

    # 编译后返回，调用方拿到即可直接 .invoke() / .stream()
    return graph.compile()


# --- 暴露 app 供外部直接 import 使用 ---
app = build_graph()


# --- 便捷运行入口 ---
if __name__ == "__main__":
    print("=" * 60)
    print("AI 知识库 V3 — LangGraph 工作流启动（5 节点精简版）")
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
        if "cost_tracker" in node_output:
            cost = node_output["cost_tracker"].get("total_cost_yuan", 0)
            print(f"  累计成本: ¥{cost}")

    print("\n" + "=" * 60)
    print("工作流执行完毕")
    print("=" * 60)
