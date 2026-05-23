from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "watchlist.json"
ICON_PATH = PROJECT_ROOT / "assets" / "app_icon.ico"
ICON_PNG_PATH = PROJECT_ROOT / "assets" / "app_icon.png"

DEFAULT_SYMBOLS = ["1.000001", "0.399001"]
VALID_MODES = {"table", "mini", "widget", "summary"}
VALID_COLOR_MODES = {"neutral", "market"}


@dataclass
class AppConfig:
    refresh_seconds: int = 30
    topmost: bool = False
    compact: bool = False
    mode: str = "mini"
    color_mode: str = "neutral"
    opacity: float = 0.92
    minimize_to_taskbar: bool = False
    enable_tray: bool = True
    geometry: str = ""
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    pinned_symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    return AppConfig(
        refresh_seconds=max(10, int(raw.get("refresh_seconds", 30))),
        topmost=bool(raw.get("topmost", False)),
        compact=bool(raw.get("compact", False)),
        mode=_choice(raw, "mode", "mini", VALID_MODES),
        color_mode=_choice(raw, "color_mode", "neutral", VALID_COLOR_MODES),
        opacity=_clamp_float(raw.get("opacity", 0.92), 0.45, 1.0),
        minimize_to_taskbar=bool(raw.get("minimize_to_taskbar", False)),
        enable_tray=bool(raw.get("enable_tray", True)),
        geometry=str(raw.get("geometry", "")),
        symbols=[str(item).strip() for item in raw.get("symbols", DEFAULT_SYMBOLS) if str(item).strip()],
        pinned_symbols=[
            str(item).strip()
            for item in raw.get("pinned_symbols", raw.get("symbols", DEFAULT_SYMBOLS))
            if str(item).strip()
        ],
    )


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "refresh_seconds": max(10, int(config.refresh_seconds)),
        "topmost": bool(config.topmost),
        "compact": bool(config.compact),
        "mode": config.mode if config.mode in VALID_MODES else "mini",
        "color_mode": config.color_mode if config.color_mode in VALID_COLOR_MODES else "neutral",
        "opacity": _clamp_float(config.opacity, 0.45, 1.0),
        "minimize_to_taskbar": bool(config.minimize_to_taskbar),
        "enable_tray": bool(config.enable_tray),
        "geometry": config.geometry,
        "symbols": list(dict.fromkeys(config.symbols or DEFAULT_SYMBOLS)),
        "pinned_symbols": list(dict.fromkeys(config.pinned_symbols or DEFAULT_SYMBOLS)),
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _choice(raw: dict[str, Any], key: str, default: str, valid_values: set[str]) -> str:
    value = str(raw.get(key, default))
    return value if value in valid_values else default


def _clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = maximum
    return max(minimum, min(maximum, number))
