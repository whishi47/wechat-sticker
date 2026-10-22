"""生图适配层。

关键认知：CLI 无法探测宿主 Agent 自己有没有生图能力（Codex / WorkBuddy 内置的
那种），所以本模块只报告「本机配置的生图通道是否可用」。是否用 Agent 自带生图，
由 SKILL.md 规定 Agent 必须主动问用户。

provider:
  none    —— 没有可用生图通道，需要 Agent 问用户（自带生图 or 配中转站）
  openai  —— OpenAI 兼容 /v1/images/generations，覆盖绝大多数中转站
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import requests

RATIO_TO_SIZE = {
    "3:4": "1024x1536",
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "4:3": "1024x1024",
}

# 生成失败时按顺序退让的尺寸（很多中转站只认 1024x1024 / 1024x1792 / 1792x1024）
SIZE_FALLBACK = ["1024x1536", "1024x1792", "1024x1024", "1024x1024"]


class ImageGenError(Exception):
    pass


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/v1"):
        return f"{base}/images/generations"
    return f"{base}/v1/images/generations"


def capability(cfg: dict) -> dict:
    """返回生图能力状态，供 check / SKILL 判断是否需要问用户。"""
    img = cfg.get("image") or {}
    provider = img.get("provider", "none")
    if provider == "openai":
        missing = [
            k
            for k in ("base_url", "api_key", "model")
            if not str(img.get(k, "")).strip()
        ]
        if missing:
            return {
                "provider": "openai",
                "ready": False,
                "model": img.get("model", ""),
                "reason": f"配置不完整，缺：{'、'.join(missing)}",
                "ask_user": True,
            }
        return {
            "provider": "openai",
            "ready": True,
            "model": img.get("model", ""),
            "reason": "已配置 OpenAI 兼容生图通道",
            "ask_user": True,
            "ask_hint": "可以问用户：用配置的生图模型出图，还是由你自己生成/提供现成图片",
        }
    return {
        "provider": "none",
        "ready": False,
        "model": "",
        "reason": "本机没有配置生图通道",
        "ask_user": True,
        "ask_hint": (
            "必须问用户二选一：(A) 用宿主 Agent 自带的生图能力出图，把图片放进 images/ 目录；"
            "(B) 配置一个中转站生图模型（base_url + api_key + model），跑 wxpost init --image-only"
        ),
    }


def probe(cfg: dict, timeout: int = 15) -> dict:
    """轻量连通性探测：只打 /v1/models，不产生生图费用。"""
    img = cfg.get("image") or {}
    if img.get("provider") != "openai":
        return {"ok": False, "detail": "未配置生图通道（provider=none）"}
    base = img.get("base_url", "").rstrip("/")
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {img.get('api_key','')}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"ok": False, "detail": f"连不上 {url}：{exc}"}
    if resp.status_code == 200:
        return {"ok": True, "detail": f"{url} 可访问"}
    if resp.status_code in (401, 403):
        return {"ok": False, "detail": f"API Key 被拒（HTTP {resp.status_code}），检查 api_key"}
    return {
        "ok": False,
        "detail": f"HTTP {resp.status_code}：中转站可能不支持 /v1/models，这不代表生图不可用，可直接试跑一次生图",
    }


def generate(
    cfg: dict,
    prompt: str,
    out_path: Path,
    ratio: str = "3:4",
    size: str | None = None,
    timeout: int | None = None,
) -> Path:
    """生成一张图并落盘，返回路径。"""
    img = cfg.get("image") or {}
    if img.get("provider") != "openai":
        raise ImageGenError(
            "没有可用的生图通道。要么让宿主 Agent 自己生图，要么先跑 wxpost init --image-only 配一个中转站"
        )

    base_url = str(img.get("base_url", "")).rstrip("/")
    api_key = str(img.get("api_key", ""))
    model = str(img.get("model", ""))
    if not (base_url and api_key and model):
        raise ImageGenError("生图配置不完整（base_url / api_key / model 都要有）")

    url = _endpoint(base_url)
    header = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    want = size or img.get("size") or RATIO_TO_SIZE.get(ratio, "1024x1536")
    order = [want] + [s for s in SIZE_FALLBACK if s != want][:3]
    wait = timeout or int(img.get("timeout", 180))

    last_err = ""
    for candidate in order:
        payload = {"model": model, "prompt": prompt, "n": 1, "size": candidate}
        for attempt in range(2):
            try:
                resp = requests.post(
                    url, headers=header, json=payload, timeout=wait
                )
            except requests.RequestException as exc:
                last_err = f"网络错误：{exc}"
                time.sleep(2)
                continue

            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    raise ImageGenError("生图接口返回的不是 JSON，检查 base_url 是否指向中转站根路径")
                return _save_image(body, out_path)

            text = resp.text[:300]
            if resp.status_code in (400, 422) and ("size" in text.lower() or candidate != want):
                last_err = f"尺寸 {candidate} 不被接受：{text}"
                break  # 换下一个尺寸
            if resp.status_code in (401, 403):
                raise ImageGenError(f"API Key 被拒（HTTP {resp.status_code}）：{text}")
            if resp.status_code == 429:
                last_err = f"限流（429）：{text}"
                time.sleep(5)
                continue
            if resp.status_code == 404:
                raise ImageGenError(
                    f"接口不存在（404）：{url}\n检查 base_url，常见是少了或多了 /v1。返回：{text}"
                )
            raise ImageGenError(f"生图失败 HTTP {resp.status_code}：{text}")

    raise ImageGenError(f"生图失败，所有尺寸都试过了。最后一次原因：{last_err}")


def _save_image(body: dict, out_path: Path) -> Path:
    data = body.get("data") or []
    if not data:
        raise ImageGenError(f"返回里没有图片数据：{str(body)[:300]}")
    item = data[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
        out_path.write_bytes(raw)
        return out_path
    if item.get("url"):
        try:
            resp = requests.get(item["url"], timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ImageGenError(f"图片下载失败：{exc}") from exc
        out_path.write_bytes(resp.content)
        return out_path
    raise ImageGenError(f"返回结构不认识（既无 b64_json 也无 url）：{str(item)[:300]}")


def batch(
    cfg: dict,
    prompts: list[str],
    out_dir: Path,
    ratio: str = "3:4",
    prefix: str = "",
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, prompt in enumerate(prompts, start=1):
        name = f"{prefix}{idx:02d}.png"
        paths.append(generate(cfg, prompt, out_dir / name, ratio=ratio))
    return paths
