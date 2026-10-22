"""wxpost 配置管理。

配置集中在 ~/.wxpost/config.json（0600），与任何 Agent 客户端解耦。
支持多公众号：accounts 下每个 key 是一个账号，active_account 指当前默认。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("WXPOST_HOME") or (Path.home() / ".wxpost"))
CONFIG_PATH = CONFIG_DIR / "config.json"
PROFILE_DIR = CONFIG_DIR / "profiles"
TOKEN_CACHE = CONFIG_DIR / ".token_cache"

DEFAULT_CONFIG = {
    "active_account": "",
    "accounts": {},
    "image": {
        "provider": "none",
        "base_url": "",
        "api_key": "",
        "model": "",
        "size": "",
        "timeout": 180,
    },
    "llm": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "defaults": {
        "cover_ratio": "1_1",
        "image_count": 4,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    },
}


class ConfigError(Exception):
    pass


def ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _harden(CONFIG_DIR)


def _harden(path: Path) -> None:
    """尽量收紧目录权限（Windows 上 chmod 效果有限，失败不阻塞）。"""
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def _harden_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{CONFIG_PATH}（{exc}）") from exc
    return _deep_merge(DEFAULT_CONFIG, raw)


def save(cfg: dict) -> None:
    ensure_dir()
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _harden_file(CONFIG_PATH)


def resolve_account(cfg: dict, name: str | None = None) -> str:
    """确定要操作的账号 key。"""
    accounts = cfg.get("accounts") or {}
    if name:
        if name not in accounts:
            known = "、".join(accounts.keys()) or "（还没配置任何账号）"
            raise ConfigError(f"账号 '{name}' 不存在。已配置的账号：{known}")
        return name
    active = cfg.get("active_account") or ""
    if active and active in accounts:
        return active
    if len(accounts) == 1:
        return next(iter(accounts))
    if not accounts:
        raise ConfigError("还没有配置任何公众号账号，先跑：wxpost init")
    known = "、".join(accounts.keys())
    raise ConfigError(f"配置了多个账号，请用 --account 指定。可选：{known}")


def get_account(cfg: dict, name: str | None = None) -> tuple[str, dict]:
    key = resolve_account(cfg, name)
    acct = cfg["accounts"][key]
    if not acct.get("app_id") or not acct.get("app_secret"):
        raise ConfigError(f"账号 '{key}' 缺少 app_id 或 app_secret，先跑：wxpost init --account {key}")
    return key, acct


def image_ready(cfg: dict) -> bool:
    img = cfg.get("image") or {}
    if img.get("provider") == "openai":
        return bool(img.get("base_url") and img.get("api_key") and img.get("model"))
    return False


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"


def summary(cfg: dict) -> dict:
    """给 check / 给 Agent 读的结构化摘要，永不包含明文密钥。"""
    accounts = []
    for key, acct in (cfg.get("accounts") or {}).items():
        accounts.append(
            {
                "account": key,
                "app_id": mask(acct.get("app_id", ""), 6),
                "author": acct.get("author", ""),
                "active": key == cfg.get("active_account"),
            }
        )
    img = cfg.get("image") or {}
    llm = cfg.get("llm") or {}
    return {
        "config_path": str(CONFIG_PATH),
        "accounts": accounts,
        "active_account": cfg.get("active_account") or "",
        "image": {
            "provider": img.get("provider", "none"),
            "model": img.get("model", ""),
            "base_url": img.get("base_url", ""),
            "api_key": mask(img.get("api_key", "")),
            "ready": image_ready(cfg),
        },
        "llm": {
            "model": llm.get("model", "") or "(未配置，用 Agent 自己写)",
            "base_url": llm.get("base_url", "") or "(复用生图的 base_url)",
            "api_key": mask(llm.get("api_key", "")),
        },
        "defaults": cfg.get("defaults", {}),
    }


def die(msg: str, code: int = 1) -> None:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)
