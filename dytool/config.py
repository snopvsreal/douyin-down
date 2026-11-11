from pathlib import Path

import yaml


def load(path: str | Path = "config.yaml") -> dict:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Không thấy {path}. Chạy: cp config.example.yaml config.yaml")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = Path(cfg.get("data_dir", "data"))
    for sub in ("raw", "edited", "work"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    cfg["_data"] = data
    return cfg
