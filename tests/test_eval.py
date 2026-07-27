"""评测运行脚本。MCP Server 必须先启动。

用法：
  终端 1: MCP_TRANSPORT=http python -m src.mcp_server.server
  终端 2: python tests/test_eval.py

输出：每题得分 + 汇总报告 + data/eval_results.json
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage
from src.agent.config_loader import load_config
from src.agent.graph import build_graph
from src.agent.state import AgentState


EVAL_FILE = Path(__file__).resolve().parent.parent / "data" / "eval_questions.json"
RESULT_FILE = Path(__file__).resolve().parent.parent / "data" / "eval_results.json"


def load_questions() -> list[dict]:
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def check_tools(expected: list[str], actual_keys: list[str]) -> bool:
    """检查预期工具是否全部被调用（startswith 匹配，兼容 #0/#1 后缀）。"""
    if not expected:
        return True
    return all(
        any(key.startswith(tool) for key in actual_keys)
        for tool in expected
    )


def count_anomaly_types(anomalies: list[dict]) -> int:
    """统计命中了多少种不同类型的异常规则。"""
    return len(set(a.get("type", "") for a in anomalies))


async def run_one(graph, q: dict) -> dict:
    """执行单条评测。"""
    state: AgentState = {
        "user_query": q["query"],
        "messages": [HumanMessage(content=q["query"])],
        "config": load_config(),
        "error": "",
    }
    thread_id = f"eval-{q['id']}"
    result = await graph.ainvoke(state, {"configurable": {"thread_id": thread_id}})

    intent = result.get("intent", "")
    tool_results = result.get("tool_results", {})
    anomalies = result.get("anomalies", [])
    suggestions = result.get("suggestions", [])

    # 打分
    score = 0.0
    details = []

    # intent 正确 +0.5
    intent_ok = intent == q["expected_intent"]
    if intent_ok:
        score += 0.5
        details.append("intent")
    else:
        details.append(f"intent✗({intent})")

    # 预期工具全部调了 +0.5
    tools_ok = check_tools(q["expected_tools"], list(tool_results.keys()))
    if tools_ok:
        score += 0.5
        details.append("tools")
    else:
        details.append(f"tools✗(got {list(tool_results.keys())})")

    # anomaly 规则有命中 +0.5（仅当题目要求时）
    if q.get("expect_anomalies") and q["id"] != 8:
        rule_count = count_anomaly_types(anomalies)
        if rule_count >= 1:
            score += 0.5
            details.append(f"anomalies({rule_count} rules)")
        else:
            details.append("anomalies✗")
    elif q["id"] == 8:
        # 全扫描题：命中 ≥ 3 条规则
        rule_count = count_anomaly_types(anomalies)
        if rule_count >= 3:
            score += 1.0
            details.append(f"scan({rule_count} rules)")
        else:
            details.append(f"scan✗({rule_count} rules)")

    return {
        "id": q["id"],
        "label": q["label"],
        "intent": intent,
        "tools_called": list(tool_results.keys()),
        "anomaly_count": len(anomalies),
        "anomaly_types": count_anomaly_types(anomalies),
        "suggestions_count": len(suggestions),
        "score": score,
        "max_score": 2.0 if q["id"] == 8 else (1.5 if q.get("expect_anomalies") else 1.0),
        "details": details,
    }


async def main():
    questions = load_questions()
    print(f"加载 {len(questions)} 条测试题\n")

    print("正在连接 MCP Server...")
    graph = await build_graph()
    print("开始评测\n")

    results = []
    total_score = 0.0
    total_max = 0.0

    for q in questions:
        label = f"[{q['id']}/{len(questions)}] {q['label']}"
        print(f"{label}: {q['query']}")
        try:
            r = await run_one(graph, q)
        except Exception as e:
            r = {
                "id": q["id"], "label": q["label"],
                "intent": "error", "tools_called": [],
                "anomaly_count": 0, "anomaly_types": 0,
                "suggestions_count": 0,
                "score": 0, "max_score": 1.0,
                "details": [f"error: {e}"],
            }
        results.append(r)
        total_score += r["score"]
        total_max += r["max_score"]
        detail_str = " | ".join(r["details"])
        print(f"  {detail_str}")
        print(f"  score: {r['score']:.1f}/{r['max_score']:.1f}\n")

    # 汇总
    pct = total_score / total_max * 100 if total_max else 0
    print("=" * 60)
    print(f"评测完成: {total_score:.1f}/{total_max:.1f} ({pct:.0f}%)")
    print(f"共 {len(questions)} 题 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 写结果文件
    report = {
        "date": datetime.now().isoformat(),
        "total_score": total_score,
        "total_max": total_max,
        "percentage": round(pct, 1),
        "results": results,
    }
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {RESULT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
