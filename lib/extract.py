"""从链接提取内容：公众号文章 / 微信贴图 / 小红书笔记。

用途是「参考」——把别人一篇写得好的图文拆成素材（标题、正文、图片），
喂给后续的文案重写和配图设计，而不是原样搬运。

平台现实：
- 公众号文章 / 微信贴图：公开可访问，HTML 结构稳定，能直接解析。
- 小红书：有反爬，多数情况拿不到。抓不到会明确报错并给替代方案，绝不假装成功。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

PLATFORM_PATTERNS = [
    ("wechat", re.compile(r"^https?://mp\.weixin\.qq\.com/s", re.I)),
    ("xiaohongshu", re.compile(r"^https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)", re.I)),
    ("xhs_short", re.compile(r"^https?://xhslink\.com/", re.I)),
]


class ExtractError(Exception):
    pass


def detect(url: str) -> str:
    for name, pat in PLATFORM_PATTERNS:
        if pat.search(url.strip()):
            return name
    return "unknown"


def _headers(referer: str = "") -> dict:
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if referer:
        h["Referer"] = referer
    return h


def fetch_html(url: str, timeout: int = 25) -> tuple[str, str]:
    """返回 (最终URL, HTML)。短链会自动跟随跳转。"""
    try:
        resp = requests.get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        raise ExtractError(f"抓取失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ExtractError(
            f"抓取失败 HTTP {resp.status_code}。链接可能需要登录，或已失效（公众号文章被删/设为私密）"
        )
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.url, resp.text


VOID_TAGS = {
    "img", "br", "hr", "input", "meta", "link", "source",
    "area", "base", "col", "embed", "param", "track", "wbr",
}


class _WeChatParser(HTMLParser):
    """从公众号文章页里抽标题、作者、正文纯文本和正文图片。

    用标签栈维护层级，避免「内部子元素结束时误判为容器结束」。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.images: list[str] = []
        self.meta: dict[str, str] = {}
        self._stack: list[str] = []
        self._content_level = 0
        self._title_level = 0
        self._author_level = 0
        self._buf: list[str] = []

    @property
    def text(self) -> str:
        raw = "".join(self._buf)
        raw = re.sub(r"[ \t\u3000\xa0]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{2,}", "\n\n", raw).strip()
        return raw

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                self.meta[key] = a["content"]

        if tag not in VOID_TAGS:
            self._stack.append(tag)
        level = len(self._stack)

        if tag == "div" and a.get("id") == "js_content" and not self._content_level:
            self._content_level = level
        if tag == "h1" and a.get("id") == "activity-name" and not self._title_level:
            self._title_level = level
        if tag in ("a", "span") and a.get("id") == "js_name" and not self._author_level:
            self._author_level = level

        if self._content_level:
            if tag in ("p", "br", "section", "div", "li", "tr"):
                self._buf.append("\n")
            if tag == "img":
                src = a.get("data-src") or a.get("src") or ""
                if src.startswith("http"):
                    self.images.append(src)
                self._buf.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        level = len(self._stack)
        if self._content_level and level == self._content_level:
            self._content_level = 0
        if self._title_level and level == self._title_level:
            self._title_level = 0
        if self._author_level and level == self._author_level:
            self._author_level = 0

        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        elif tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._title_level:
            self.title += data
        if self._author_level:
            self.author += data
        if self._content_level:
            self._buf.append(data)


def extract_wechat(html: str) -> dict:
    p = _WeChatParser()
    try:
        p.feed(html)
    except Exception as exc:  # 解析容错
        raise ExtractError(f"页面解析失败：{exc}") from exc

    title = (p.title or p.meta.get("og:title") or p.meta.get("twitter:title") or "").strip()
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        title = (m.group(1) if m else "").strip()

    author = (p.author or p.meta.get("og:article:author") or "").strip()
    content = p.text
    images = _dedupe(p.images)

    if not content and not images:
        if "环境异常" in html or "验证" in html[:5000]:
            raise ExtractError("公众号返回了验证页，这条链接需要人工在浏览器打开后复制内容")
        raise ExtractError("没能从页面里解析出正文，可能是新版页面结构或链接已失效")

    is_picture = _looks_like_picture_message(content, images)
    return {
        "platform": "wechat",
        "kind": "picture" if is_picture else "article",
        "title": title,
        "author": author,
        "content": content,
        "images": images,
    }


def _looks_like_picture_message(content: str, images: list[str]) -> bool:
    """图片多、文字少 → 大概率本来就是贴图（图片消息）。"""
    if not images:
        return False
    return len(images) >= 3 and len(content) < 400


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_xhs(html: str, note_id: str = "") -> dict:
    """从小红书页面里抠 __INITIAL_STATE__。反爬强，失败是常态。"""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        if "登录" in html or "login" in html.lower()[:3000]:
            raise ExtractError(
                "小红书这条链接要求登录，抓不到内容。\n"
                "替代方案：① 把正文直接粘给我；② 截图或导出图片给我；③ 用 cookies 方式（本工具暂不支持）"
            )
        raise ExtractError("小红书页面结构变了，没找到内容数据。把正文粘给我更靠谱")

    raw = m.group(1).replace("undefined", "null")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractError(f"小红书数据解析失败：{exc}。建议直接粘正文给我") from exc

    note = None
    detail = (state.get("note") or {}).get("noteDetailMap") or {}
    if note_id and note_id in detail:
        note = (detail[note_id] or {}).get("note")
    if note is None:
        for _, v in detail.items():
            note = (v or {}).get("note")
            if note:
                break
    if not note:
        raise ExtractError("小红书返回的数据里没有笔记内容（可能被风控）。建议直接粘正文给我")

    title = str(note.get("title", "")).strip()
    desc = str(note.get("desc", "")).strip()
    images = []
    for item in note.get("imageList") or []:
        url = item.get("urlDefault") or item.get("urlPre") or item.get("url") or ""
        if url:
            images.append(url)

    if not (title or desc or images):
        raise ExtractError("小红书笔记内容为空，可能已删除或仅自己可见")

    return {
        "platform": "xiaohongshu",
        "kind": "note",
        "title": title,
        "author": str((note.get("user") or {}).get("nickname", "")).strip(),
        "content": desc,
        "images": _dedupe(images),
    }


def _note_id_from_url(url: str) -> str:
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-fA-F]+)", url)
    return m.group(1) if m else ""


def download_image(url: str, dest: Path, referer: str = "https://mp.weixin.qq.com/") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, headers=_headers(referer), timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ExtractError(f"图片下载失败：{exc}") from exc
    if len(resp.content) < 1024:
        raise ExtractError("图片内容过小，可能被防盗链拦截")

    ext = ".jpg"
    ctype = resp.headers.get("Content-Type", "")
    if "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    final = dest.with_suffix(ext)
    final.write_bytes(resp.content)
    return final


def extract(url: str) -> dict:
    platform = detect(url)
    if platform == "unknown":
        raise ExtractError(
            "认不出这个链接。目前支持：\n"
            "  · 公众号文章 / 微信贴图：https://mp.weixin.qq.com/s/...\n"
            "  · 小红书笔记：https://www.xiaohongshu.com/explore/... 或 https://xhslink.com/...\n"
            "其他平台请把正文和图片直接给我。"
        )

    final_url, html = fetch_html(url)
    if platform in ("xiaohongshu", "xhs_short"):
        info = extract_xhs(html, _note_id_from_url(final_url or url))
        info["source_url"] = final_url
        return info

    info = extract_wechat(html)
    info["source_url"] = final_url
    return info
