"""按公众号账号隔离的长期记忆（风格档案）。

每个账号一份 ~/.wxpost/profiles/<account>/profile.json，记录这个号
"喜欢什么风格、怎么排版、哪些词不能碰"。Agent 写文案前必须先读它，
发布后由 CLI 自动累加统计。

三层结构：
  1. style/layout  —— 结构化偏好，可直接拼成生成提示词
  2. rules          —— 禁用词、偏好词、选题倾向
  3. notes          —— 自然语言笔记，记录"以后都这样"的临时指令
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config

DEFAULT_STYLE = {
    "tone": "口语化，像跟朋友聊天，不端着",
    "person": "第一人称",
    "sentence": "短句为主，一句不超过 25 字",
    "emoji": "medium",
    "title_style": "12-20 字，带具体数字或悬念钩子，不用「震惊」「必看」",
    "body_length": [200, 400],
    "hashtag_count": 4,
    "signature": "",
}

DEFAULT_LAYOUT = {
    "image_count": 4,
    "cover_ratio": "1_1",
    "image_style": "",
}

DEFAULT_RULES = {
    "banned_words": [],
    "preferred_words": [],
    "liked_topics": [],
    "avoided_topics": [],
}

DEFAULT_PROFILE = {
    "account": "",
    "display_name": "",
    "style": DEFAULT_STYLE,
    "layout": DEFAULT_LAYOUT,
    "rules": DEFAULT_RULES,
    "notes": [],
    "stats": {"published_count": 0, "last_published_at": "", "recent_titles": []},
}

STYLE_KEYS = set(DEFAULT_STYLE)
LAYOUT_KEYS = set(DEFAULT_LAYOUT)
RULE_KEYS = set(DEFAULT_RULES)


def _profile_path(account: str) -> Path:
    return config.PROFILE_DIR / account / "profile.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def exists(account: str) -> bool:
    return _profile_path(account).exists()


def load(account: str) -> dict:
    path = _profile_path(account)
    if not path.exists():
        data = json.loads(json.dumps(DEFAULT_PROFILE))
        data["account"] = account
        data["display_name"] = account
        return data
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = _deep_merge(DEFAULT_PROFILE, raw)
    data["account"] = account
    return data


def save(account: str, data: dict) -> None:
    path = _profile_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_accounts() -> list[str]:
    if not config.PROFILE_DIR.exists():
        return []
    return sorted(p.name for p in config.PROFILE_DIR.iterdir() if p.is_dir())


def _coerce(value: str):
    v = value.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    if v.startswith("[") or v.startswith("{"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    if "," in v and v.replace(",", "").replace(" ", "").isalnum() is False:
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


def set_field(account: str, field: str, value: str) -> tuple[bool, str]:
    """写一个结构化字段。支持 style.xxx / layout.xxx / rules.xxx 分组。"""
    data = load(account)
    group, _, key = field.partition(".")
    if not key:
        # 裸字段名：按 style > layout > rules 顺序猜
        for g, keys in (("style", STYLE_KEYS), ("layout", LAYOUT_KEYS), ("rules", RULE_KEYS)):
            if field in keys:
                group, key = g, field
                break
        else:
            return False, f"未知字段 '{field}'。可用：style.* / layout.* / rules.*（跑 wxpost profile fields 看全量）"

    if group not in ("style", "layout", "rules"):
        return False, f"未知分组 '{group}'，只支持 style / layout / rules"

    coerced = _coerce(value)
    if group == "rules" and key in ("banned_words", "preferred_words", "liked_topics", "avoided_topics"):
        if isinstance(coerced, str):
            coerced = [x.strip() for x in coerced.replace("，", ",").split(",") if x.strip()]
        if not isinstance(coerced, list):
            return False, f"{field} 需要逗号分隔的多个值"

    data[group][key] = coerced
    save(account, data)
    return True, f"{account} · {group}.{key} = {json.dumps(coerced, ensure_ascii=False)}"


def add_note(account: str, text: str) -> None:
    data = load(account)
    data["notes"].append(
        {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text.strip()}
    )
    save(account, data)


def record_publish(account: str, title: str, media_id: str) -> None:
    data = load(account)
    stats = data.setdefault("stats", {"published_count": 0, "last_published_at": "", "recent_titles": []})
    stats["published_count"] = int(stats.get("published_count", 0)) + 1
    stats["last_published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    titles = stats.get("recent_titles") or []
    titles.insert(0, {"title": title, "media_id": media_id, "at": stats["last_published_at"]})
    stats["recent_titles"] = titles[:20]
    save(account, data)


def to_prompt(account: str) -> str:
    """把风格档案渲染成一段写作要求，直接给 Agent 读。"""
    p = load(account)
    s, lay, r = p["style"], p["layout"], p["rules"]
    emoji_map = {
        "none": "不用 emoji，纯文字",
        "light": "少量 emoji（全文 3-6 个），只放在最关键的一两处，起提神作用",
        "medium": "适量 emoji（全文 8-15 个）：每段开头一个做视觉锚点，分点用 emoji 序号，关键数字前加提示符号",
        "heavy": "较密 emoji（接近原生小红书）：几乎每段都有，分点全用 emoji 序号，情绪词后面也带",
    }
    lines = [
        f"## 账号「{p.get('display_name') or account}」的写作要求",
        "",
        f"- 语气：{s.get('tone')}",
        f"- 人称：{s.get('person')}",
        f"- 句式：{s.get('sentence')}",
        f"- emoji：{emoji_map.get(str(s.get('emoji')), s.get('emoji'))}",
        f"- 标题：{s.get('title_style')}（微信硬限制 ≤32 字，超了直接失败）",
        f"- 正文长度：{s.get('body_length')[0]}-{s.get('body_length')[1]} 字",
        f"- 标签数量：{s.get('hashtag_count')} 个 #话题",
        f"- 配图数量：{lay.get('image_count')} 张（微信上限 20 张，首张自动作封面）",
        f"- 封面裁剪比例：{lay.get('cover_ratio')}（只支持 1_1 / 16_9 / 2.35_1）",
    ]
    if s.get("signature"):
        lines.append(f"- 结尾固定语：{s['signature']}")
    if lay.get("image_style"):
        lines.append(f"- 配图统一视觉风格：{lay['image_style']}（每张图的提示词都要带上这段）")
    if r.get("banned_words"):
        lines.append(f"- 禁用词（出现即返工）：{'、'.join(r['banned_words'])}")
    if r.get("preferred_words"):
        lines.append(f"- 惯用词（可自然带入）：{'、'.join(r['preferred_words'])}")
    if r.get("liked_topics"):
        lines.append(f"- 偏爱选题：{'、'.join(r['liked_topics'])}")
    if r.get("avoided_topics"):
        lines.append(f"- 避开选题：{'、'.join(r['avoided_topics'])}")
    notes = p.get("notes") or []
    if notes:
        lines.append("")
        lines.append("### 历史记忆（用户明确要求过的，优先级高于上面的默认值）")
        for n in notes[-15:]:
            lines.append(f"- [{n.get('date','')}] {n.get('text','')}")
    stats = p.get("stats") or {}
    if stats.get("published_count"):
        lines.append("")
        lines.append(f"### 已发布 {stats['published_count']} 篇，最近标题：")
        for t in (stats.get("recent_titles") or [])[:5]:
            lines.append(f"- {t.get('title','')}")
    return "\n".join(lines)
