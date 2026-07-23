"""
加载 metrics.yaml，失败返回空配置不崩。
"""
import yaml
from pathlib import Path


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config" / "metrics.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}
