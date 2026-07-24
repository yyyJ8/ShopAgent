"""
Agent 运行日志。结构化记录每个节点的输入输出，便于排查问题。
"""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_logger = logging.getLogger("ozon-agent")
_logger.setLevel(logging.DEBUG)

# ── 文件 handler（详细日志）──
_file_handler = logging.FileHandler(
    LOG_DIR / f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
))
_logger.addHandler(_file_handler)

# ── 控制台 handler（简洁输出）──
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_console)


def log_node_start(node: str, state: dict):
    """节点入口日志。"""
    _logger.info(f"▶ {node}")
    _logger.debug(f"  intent={state.get('intent', '?')}, query={state.get('user_query', '')[:80]}")


def log_node_end(node: str, output: dict):
    """节点出口日志。"""
    for key, val in output.items():
        if key == "messages":
            msgs = val if isinstance(val, list) else [val]
            for m in msgs:
                mtype = type(m).__name__
                preview = str(m.content)[:120] if hasattr(m, "content") else ""
                has_tools = hasattr(m, "tool_calls") and m.tool_calls
                if has_tools:
                    _logger.info(f"  ← {node} → {mtype} tool_calls={[t['name'] for t in m.tool_calls]}")
                else:
                    _logger.debug(f"  ← {node} → {mtype} {preview}")
        elif key == "tool_results":
            for name, result in (val or {}).items():
                rc = result.get("row_count", "?")
                err = result.get("error")
                if err:
                    _logger.warning(f"  ← {node} {name}: ERROR {err}")
                else:
                    _logger.info(f"  ← {node} {name}: {rc} rows")
        elif key == "final_answer":
            _logger.info(f"  ← {node} final_answer ({len(str(val))} chars)")
        elif key == "analysis":
            _logger.info(f"  ← {node} analysis ({len(str(val))} chars)")
        elif key == "anomalies":
            _logger.info(f"  ← {node} anomalies: {len(val) if isinstance(val, list) else 0} items")
        elif key == "suggestions":
            _logger.info(f"  ← {node} suggestions: {len(val) if isinstance(val, list) else 0} items")
        elif key == "intent":
            _logger.info(f"  ← {node} intent={val}")
        elif key == "error" and val:
            _logger.error(f"  ← {node} error: {val}")
    _logger.debug(f"  ← {node} raw: {json.dumps({k: str(v)[:200] for k, v in output.items() if k != 'messages'}, ensure_ascii=False)}")
