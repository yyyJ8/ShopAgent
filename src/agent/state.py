"""
Agent 状态定义。
"""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    messages: list              # LangGraph 消息历史 (HumanMsg, AIMsg, ToolMsg)
    user_query: str             # 本轮原始问题

    # ── 意图 & 实体 ──
    intent: str                 # "lookup" | "anomaly" | "advice" | "chat"
    entities: dict              # {date_range, sku_ids, metrics, store_id, ...}

    # ── 工具执行 ──
    tool_results: dict          # {tool_name: {data: [...], row_count: N, error?: str}}

    # ── 分析流水线 ──
    analysis: str               # 数据解读文本
    anomalies: list             # [{type, severity, detail, attribution, ...}]
    suggestions: list           # 可执行运营建议

    # ── 配置 & 最终输出 ──
    config: dict                # metrics.yaml 加载结果
    final_answer: str           # 给用户的最终回答
    error: str                  # 全局错误标记

    # ── 数据完整性环 ──
    plan_iteration: int         # plan 轮次（0 起始，≥2 熔断）
    missing_sources: list[str]  # 上一轮 data_check 发现缺失的数据源名
    skipped_rules: list[str]    # 因数据源缺失被跳过的规则名
