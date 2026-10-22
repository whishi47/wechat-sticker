"""微信公众号 API 封装：token、永久素材、图片消息草稿、发布。

只做官方 openapi 的确定性调用，不含任何业务判断。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from . import config

API = "https://api.weixin.qq.com"
TIMEOUT = 30

# 官方错误码 → 人话
ERRCODE_HINT = {
    40001: "access_token 失效（已自动重试过一次；仍失败请检查 AppSecret）",
    40002: "grant_type 不对，检查请求参数",
    40013: "AppID 无效：检查 app_id 是否复制完整、是否属于这个公众号",
    40007: "media_id 无效：图片可能没上传成功，或不属于这个公众号",
    40097: "参数有误：常见是 title 超过 32 字、content 超过 2 万字、或 cover_ratio 不在允许值内",
    40125: "AppSecret 错误，去公众号后台「设置与开发 → 基本配置」重新复制",
    40164: "调用 IP 不在白名单：把下面这个 IP 加进「基本配置 → IP 白名单」再重试",
    41001: "缺少 access_token",
    42001: "access_token 已过期（已自动重试）",
    44002: "请求体为空，检查 JSON 是否被截断",
    45001: "图片超过 2MB，先压缩再上传",
    45003: "标题超出长度限制（≤32 字）",
    45009: "接口调用次数超限（公众号每日配额），等第二天或减少频繁上传",
    48001: "接口未授权：该公众号没有此 API 权限，通常需要认证的服务号/订阅号",
    53404: "账号被限制带货能力，删掉商品信息重试",
    53405: "插入的商品信息有误",
    53406: "请先开通带货能力",
}


class WeChatError(Exception):
    def __init__(self, errcode: int, errmsg: str, extra: str = ""):
        self.errcode = errcode
        self.errmsg = errmsg
        self.extra = extra
        hint = ERRCODE_HINT.get(errcode, "未知错误，见官方错误码文档")
        msg = f"[{errcode}] {errmsg}｜{hint}"
        if extra:
            msg += f"\n{extra}"
        super().__init__(msg)


def _token_cache_load() -> dict:
    try:
        return json.loads(config.TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _token_cache_save(data: dict) -> None:
    config.ensure_dir()
    config.TOKEN_CACHE.write_text(json.dumps(data), encoding="utf-8")


def _request(method: str, url: str, **kwargs) -> dict:
    last = None
    for attempt in range(3):
        try:
            resp = requests.request(method, url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
            time.sleep(1.5 * (2**attempt))
        except requests.HTTPError as exc:
            raise WeChatError(-1, f"HTTP 层错误：{exc}") from exc
        except ValueError as exc:
            raise WeChatError(-1, "微信返回的不是 JSON，可能被网络中间层拦截") from exc
    raise WeChatError(-1, f"网络请求连续 3 次失败：{last}")


def get_access_token(account_key: str, app_id: str, app_secret: str, force: bool = False) -> str:
    cache = _token_cache_load()
    entry = cache.get(account_key) or {}
    if not force and entry.get("app_id") == app_id and entry.get("expires_at", 0) > time.time() + 300:
        return entry["access_token"]

    data = _request(
        "GET",
        f"{API}/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        },
    )
    if "access_token" not in data:
        raise WeChatError(data.get("errcode", -1), data.get("errmsg", "获取 access_token 失败"))
    token = data["access_token"]
    cache[account_key] = {
        "app_id": app_id,
        "access_token": token,
        "expires_at": time.time() + int(data.get("expires_in", 7200)),
    }
    _token_cache_save(cache)
    return token


def upload_permanent_image(token: str, image_path: Path) -> dict:
    """上传永久素材（type=image），返回 {media_id, url}。

    图片消息草稿要求 image_media_id 必须是永久 MediaID，临时素材不行。
    """
    with image_path.open("rb") as fh:
        files = {"media": (image_path.name, fh, "image/jpeg")}
        data = _request(
            "POST",
            f"{API}/cgi-bin/material/add_material",
            params={"access_token": token, "type": "image"},
            files=files,
        )
    if "media_id" not in data:
        raise WeChatError(
            data.get("errcode", -1),
            data.get("errmsg", "上传永久素材失败"),
            f"文件：{image_path}（{image_path.stat().st_size / 1024:.0f} KB）",
        )
    return {"media_id": data["media_id"], "url": data.get("url", "")}


def _post_json(url: str, token: str, payload: dict) -> dict:
    # 微信要求中文不转义，手动序列化
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(
        "POST",
        url,
        params={"access_token": token},
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def crop_percent_list(ratio: str, img_w: int, img_h: int) -> list[dict]:
    """按目标比例算居中裁剪框（归一化坐标）。"""
    ratio_map = {"1_1": 1.0, "16_9": 16 / 9, "2.35_1": 2.35}
    target = ratio_map.get(ratio, 1.0)
    if not img_w or not img_h:
        return [{"ratio": ratio, "x1": "0", "y1": "0", "x2": "1", "y2": "1"}]
    src = img_w / img_h
    if src > target:
        # 原图更宽 → 左右居中裁
        new_w = target / src
        x1 = (1 - new_w) / 2
        box = (x1, 0.0, 1 - x1, 1.0)
    elif src < target:
        # 原图更高 → 上下居中裁
        new_h = src / target
        y1 = (1 - new_h) / 2
        box = (0.0, y1, 1.0, 1 - y1)
    else:
        box = (0.0, 0.0, 1.0, 1.0)
    return [
        {
            "ratio": ratio,
            "x1": f"{box[0]:.6f}",
            "y1": f"{box[1]:.6f}",
            "x2": f"{box[2]:.6f}",
            "y2": f"{box[3]:.6f}",
        }
    ]


def add_newspic_draft(
    token: str,
    *,
    title: str,
    content: str,
    image_items: list[dict],
    cover_ratio: str = "1_1",
    author: str = "",
    need_open_comment: int = 1,
    only_fans_can_comment: int = 0,
) -> str:
    """新增图片消息（贴图 / 小绿书）草稿，返回 draft media_id。

    image_items: [{"media_id":..., "width":..., "height":...}]，首张自动作封面。
    """
    image_list = [{"image_media_id": it["media_id"]} for it in image_items]
    first = image_items[0]
    article = {
        "article_type": "newspic",
        "title": title,
        "content": content,
        "need_open_comment": int(need_open_comment),
        "only_fans_can_comment": int(only_fans_can_comment),
        "image_info": {"image_list": image_list},
        "cover_info": {
            "crop_percent_list": crop_percent_list(
                cover_ratio, first.get("width", 0), first.get("height", 0)
            )
        },
    }
    if author:
        article["author"] = author

    data = _post_json(f"{API}/cgi-bin/draft/add", token, {"articles": [article]})
    if "media_id" not in data:
        raise WeChatError(data.get("errcode", -1), data.get("errmsg", "建草稿失败"))
    return data["media_id"]


def submit_publish(token: str, draft_media_id: str) -> str:
    """把草稿提交发布（不可撤销），返回 publish_id。"""
    data = _post_json(
        f"{API}/cgi-bin/freepublish/submit", token, {"media_id": draft_media_id}
    )
    if "publish_id" not in data:
        raise WeChatError(data.get("errcode", -1), data.get("errmsg", "提交发布失败"))
    return data["publish_id"]


def publish_status(token: str, publish_id: str) -> dict:
    return _post_json(f"{API}/cgi-bin/freepublish/get", token, {"publish_id": publish_id})


def draft_count(token: str) -> int:
    data = _request("GET", f"{API}/cgi-bin/draft/count", params={"access_token": token})
    if "total_count" not in data:
        raise WeChatError(data.get("errcode", -1), data.get("errmsg", "查询草稿数失败"))
    return int(data["total_count"])


def draft_delete(token: str, media_id: str) -> None:
    data = _post_json(f"{API}/cgi-bin/draft/delete", token, {"media_id": media_id})
    if data.get("errcode"):
        raise WeChatError(data["errcode"], data.get("errmsg", "删除草稿失败"))


def draft_list(token: str, offset: int = 0, count: int = 20) -> list[dict]:
    data = _post_json(
        f"{API}/cgi-bin/draft/batchget",
        token,
        {"offset": offset, "count": count, "no_content": 1},
    )
    if data.get("errcode"):
        raise WeChatError(data["errcode"], data.get("errmsg", "获取草稿列表失败"))
    out = []
    for item in data.get("item", []) or []:
        news = (item.get("content") or {}).get("news_item") or []
        head = news[0] if news else {}
        out.append(
            {
                "media_id": item.get("media_id", ""),
                "title": head.get("title", ""),
                "article_type": head.get("article_type", "news"),
                "update_time": item.get("update_time", 0),
            }
        )
    return out
