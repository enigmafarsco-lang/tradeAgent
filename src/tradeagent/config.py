"""بارگذاری پیکربندی TOML + بازنویسی با متغیرهای محیطی."""

from __future__ import annotations

import copy
import os
from pathlib import Path

try:  # پایتون ۳.۱۱+
    import tomllib
except ModuleNotFoundError:  # پایتون ۳.۱۰ (اوبونتو ۲۲.۰۴)
    import tomli as tomllib  # type: ignore[no-redef]
from typing import Any

from .util import LOG

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.toml"

# نگاشت متغیر محیطی → کلید پیکربندی
ENV_OVERRIDES: dict[str, str] = {
    "TRADEAGENT_SOURCE": "data.source",
    "TRADEAGENT_HARVESTER_DIR": "data.harvester_dir",
    "TRADEAGENT_STATE_DIR": "data.state_dir",
    "TRADEAGENT_SYMBOLS": "data.symbols",
    "TRADEAGENT_STALE_AFTER_S": "data.stale_after_s",
    "TRADEAGENT_BALANCE": "paper.starting_balance",
    "TRADEAGENT_RISK_PCT": "paper.risk_per_trade_pct",
    "TRADEAGENT_LLM_PROVIDER": "llm.provider",
    "TRADEAGENT_LLM_MODEL": "llm.model",
    "TRADEAGENT_OLLAMA_URL": "llm.ollama_url",
    "TRADEAGENT_OLLAMA_MODEL": "llm.ollama_model",
    "TRADEAGENT_PUBLISH": "publish.enabled",
    "TRADEAGENT_PUBLISH_BRANCH": "publish.branch",
    "TRADEAGENT_PUBLISH_REMOTE": "publish.remote",
    "TRADEAGENT_LOG_LEVEL": "general.log_level",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(text: str) -> Any:
    low = text.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _split(dotted: str) -> list[str]:
    return [part for part in dotted.split(".") if part]


def load_dotenv(path: Path) -> int:
    """خواندن فایل .env بدون بازنویسی متغیرهای موجود محیط. تعداد کلیدها را برمی‌گرداند."""
    if not path.is_file():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


class Config:
    """دسترسی نقطه‌ای به پیکربندی: ``cfg.get('paper.risk_per_trade_pct')``."""

    def __init__(self, data: dict[str, Any], root: Path, source: Path | None = None) -> None:
        self.data = data
        self.root = root
        self.source = source

    # -- ساخت ------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None, root: Path | None = None) -> "Config":
        root = Path(root).resolve() if root else REPO_ROOT
        cfg_path = Path(path).expanduser() if path else root / "configs" / "default.toml"
        data: dict[str, Any] = {}
        if cfg_path.exists():
            with open(cfg_path, "rb") as fh:
                data = tomllib.load(fh)
        elif cfg_path != DEFAULT_CONFIG and DEFAULT_CONFIG.exists():
            LOG.warning("پیکربندی %s پیدا نشد؛ پیش‌فرض پروژه استفاده می‌شود", cfg_path)
            with open(DEFAULT_CONFIG, "rb") as fh:
                data = tomllib.load(fh)
        cfg = cls(data, root, cfg_path if cfg_path.exists() else None)
        loaded = load_dotenv(root / ".env")
        if loaded:
            LOG.info("از .env خوانده شد: %s کلید", loaded)
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        for env_name, dotted in ENV_OVERRIDES.items():
            raw = os.environ.get(env_name)
            if raw is None or raw == "":
                continue
            if dotted.endswith("symbols"):
                self.set(dotted, [p.strip() for p in raw.split(",") if p.strip()])
            else:
                self.set(dotted, _coerce(raw))

    # -- خواندن/نوشتن ---------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in _split(dotted):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node if node is not None else default

    def set(self, dotted: str, value: Any) -> None:
        node = self.data
        keys = _split(dotted)
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    def section(self, name: str) -> dict[str, Any]:
        value = self.get(name, {})
        return dict(value) if isinstance(value, dict) else {}

    def path(self, dotted_or_value: str | Path | None, default: str | Path | None = None) -> Path:
        """مسیر مطلق از یک کلید پیکربندی (``paper.sqlite``) یا یک مسیر خام.

        اگر مقدار یک کلید شناخته‌شده باشد، مقدارش استفاده می‌شود؛ اگر شبیه مسیر
        باشد (``/`` یا ``~``) همان؛ در غیر این‌صورت ``default``.
        """
        fallback = default if default is not None else "."
        raw: Any = None
        if isinstance(dotted_or_value, Path):
            raw = dotted_or_value
        elif isinstance(dotted_or_value, str):
            resolved = self.get(dotted_or_value, None)
            if resolved is not None:
                raw = resolved
            elif "/" in dotted_or_value or dotted_or_value.startswith("~") or dotted_or_value.startswith("."):
                raw = dotted_or_value
            else:
                raw = fallback
        if raw in (None, ""):
            raw = fallback
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path

    @property
    def state_dir(self) -> Path:
        return self.path("data.state_dir", "var")

    @property
    def symbols(self) -> list[str]:
        raw = self.get("data.symbols", []) or []
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.split(",") if p.strip()]
        return [str(s) for s in raw]

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    def __repr__(self) -> str:  # pragma: no cover - دیباگ
        return f"Config(root={self.root}, source={self.source})"
