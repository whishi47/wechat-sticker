"""文案与配图策划（可选能力）。

只有当用户配了 LLM 通道才启用；没配就让 Agent 自己写。
默认复用 image 段的 base_url / api_key（中转站通常同一个 key 既能生图也能对话），
也可以在 config.json 的 llm 段单独指定。
"""

from __future__ import annotations

import json
import re

import requests


class LLMError(Exception):
    pass


def _resolve(cfg: dict) -> tuple[str, str, str]:
    llm = cfg.get("llm") or {}
    img = cfg.get("image") or {}
    base = (llm.get("base_url") or img.get("base_url") or "").rstrip("/")
    key = llm.get("api_key") or img.get("api_key") or ""
    model = llm.get("model") or ""
    return base, key, model


def capability(cfg: dict) -> dict:
    base, key, model = _resolve(cfg)
    if not model:
        return {
            "ready": False,
            "model": "",
            "reason": "没有配置对话模型（llm.model）。可以不配，改由 Agent 自己写文案和配图方案",
        }
    if not (base and key):
        return {"ready": False, "model": model, "reason": "缺 base_url 或 api_key"}
    return {"ready": True, "model": model, "reason": f"可用：{model}"}


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def chat(cfg: dict, messages: list[dict], temperature: float = 0.85, timeout: int = 120) -> str:
    base, key, model = _resolve(cfg)
    if not (base and key and model):
        raise LLMError(
            "没有配置对话模型。要么跑 wxpost init --with-llm 配一个，要么让 Agent 自己写文案"
        )

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    try:
        resp = requests.post(
            _endpoint(base),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LLMError(f"请求对话模型失败：{exc}") from exc

    if resp.status_code in (401, 403):
        raise LLMError(f"对话模型 Key 被拒（HTTP {resp.status_code}）：{resp.text[:200]}")
    if resp.status_code == 404:
        raise LLMError(
            f"对话接口不存在（404）：{_endpoint(base)}\n"
            "中转站可能只提供生图，不提供 /v1/chat/completions。把 llm.base_url 指向支持对话的地址"
        )
    if resp.status_code != 200:
        raise LLMError(f"对话模型报错 HTTP {resp.status_code}：{resp.text[:300]}")

    try:
        body = resp.json()
        return body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMError(f"返回结构不认识：{resp.text[:300]}") from exc


def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON（容忍 ```json 包裹和前后废话）。"""
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    candidate = fence.group(1) if fence else text
    candidate = candidate.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"模型没返回 JSON：{text[:300]}")
    raw = candidate[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 去掉尾随逗号再试一次
        fixed = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型返回的 JSON 解析失败：{exc}\n原文：{raw[:400]}") from exc


IMAGE_STYLE_RULES = """配图必须按这套结构设计，不要所有图长一个样：
- 第 1 张（封面）：负责点击率。强视觉冲击 + 图上直接放大字标题（3-12 个中文字），浅色或高对比背景，主体明确，留白给文字。
- 中间几张（内容）：每张只承担一个信息点，形态要有变化——信息图 / 对比图 / 步骤图 / 场景图轮换，不要连续几张同一种。
- 最后 1 张（收尾）：金句或行动引导，留白多，安静，不堆信息。
- 全系列视觉语言必须统一：同一套配色、同一种画风（插画或摄影二者择一，不要混）、同一光源方向。
- 图上出现的每一句中文都要简短、逐字准确、不写长句子，不要出现乱码或变形字母。
- 不要把正文原句照搬上图，要提炼成 5-12 字的关键词句。
- 禁止出现：真实品牌 logo、可辨认的真人肖像、复杂表格、超过 8 个字的副标题。"""


EMOJI_RULES = """正文要用 emoji 做视觉锚点，不要写成一片纯文字：
- 分点用 emoji 序号（1️⃣ 2️⃣ 3️⃣）或状态符（✅ ❌ ⚠️ 💡），绝不用「-」「1.」这类 Markdown 列表
- 每段开头放一个和内容相关的 emoji，代替小标题
- 关键数字、结论、提醒前面加提示类 emoji（👉 💡 ❗ 🎯 📌 📢）
- 只用常见 emoji（🔥 💡 ✅ ❌ ⚠️ 👉 📍 🎯 🚗 💰 📌 📢 👇），冷门符号在部分安卓机上会显示成方块
- 一行最多 1-2 个，不要连续堆三四个
- emoji 是调味不是主菜：把 emoji 全删掉后，这段文字仍然要通顺、能独立读懂"""


def build_writer_messages(
    profile_prompt: str,
    brief: str,
    count: int,
    extra: str = "",
) -> list[dict]:
    system = f"""你是微信公众号「贴图」（小绿书 / 图片消息）的资深内容编辑。

这个形态的版式是：标题 → 多张图横向轮播 → 下方一整段纯文本正文 → #标签。
注意它和小红书不一样：**图文不能交错**，正文是一整段可独立阅读的文字，图片在正文上方轮播。
正文只支持纯文本，任何 Markdown 语法（##、**、-）都不允许出现。

{EMOJI_RULES}

{profile_prompt}

{IMAGE_STYLE_RULES}

输出要求：只返回一个 JSON 对象，不要任何解释、不要 markdown 代码块之外的文字。字段：
{{
  "title": "标题，≤32 字，具体、有信息量",
  "content": "正文纯文本，可分段，不用任何 Markdown 标记",
  "hashtags": ["#话题1", "#话题2"],
  "image_prompts": ["第1张（封面）的完整生图提示词", "第2张的提示词", "...共 {count} 条"]
}}

每条 image_prompts 要写成可直接交给生图模型的完整描述：用途 → 画面主体 → 构图 → 风格 → 配色 → 光线 → 图上文字（用引号逐字写出）→ 输出比例 3:4。"""

    user = f"本次选题/要求：{brief}\n需要 {count} 张配图。"
    if extra:
        user += f"\n额外要求：{extra}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_image_plan_messages(
    profile_prompt: str,
    brief: str,
    count: int,
    existing_content: str = "",
) -> list[dict]:
    system = f"""你是微信公众号「贴图」的配图策划。只负责设计配图，不写正文。

{profile_prompt}

{IMAGE_STYLE_RULES}

输出：只返回 JSON，字段 image_prompts（字符串数组，共 {count} 条），不要其他内容。
每条要写成可直接交给生图模型的完整描述：用途 → 画面主体 → 构图 → 风格 → 配色 → 光线 → 图上文字（用引号逐字写出）→ 输出比例 3:4。"""
    user = f"主题：{brief}\n需要 {count} 张图。"
    if existing_content:
        user += f"\n已有的正文内容（配图要为它服务，但不要照搬原句上图）：\n{existing_content[:1500]}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def grid_for(count: int) -> tuple[int, int]:
    """按张数选故事板分格布局（行, 列）。"""
    if count <= 3:
        return 1, max(count, 1)
    if count == 4:
        return 2, 2
    if count <= 6:
        return 2, 3
    return 3, 3


def build_storyboard_messages(
    profile_prompt: str,
    brief: str,
    count: int,
    existing_content: str = "",
) -> list[dict]:
    """让模型把整篇内容拆成 N 个分镜，供九宫格故事板使用。"""
    system = f"""你是微信公众号「贴图」的分镜策划。把一篇内容拆成 {count} 个画面分镜。

{profile_prompt}

分镜结构要求：
- 第 1 格是封面：负责点击率，要有大字标题和强视觉主体。
- 中间各格各承担一个信息点，不要重复，形态要有变化（信息图 / 对比 / 步骤 / 场景 / 地图 / 清单轮换）。
- 最后 1 格收尾：金句或行动引导，留白多。

输出：只返回 JSON，字段 frames，长度为 {count} 的数组。每个元素：
{{
  "index": 1,
  "role": "cover | info | closing",
  "headline": "这一格要传达的一句话（不超过 14 字）",
  "scene": "画面主体与构图的简要描述（30-60 字，给画师看的那种白描）",
  "text_on_image": "这一格图面上要出现的中文，5-12 字，没有就留空字符串"
}}
不要输出任何解释。"""
    user = f"主题：{brief}\n拆成 {count} 格。"
    if existing_content:
        user += f"\n正文内容：\n{existing_content[:1500]}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def compose_storyboard_prompt(
    frames: list[dict], style: str, count: int = 0
) -> str:
    """把分镜表拼成一张九宫格故事板的生图提示词。"""
    count = count or len(frames)
    rows, cols = grid_for(count)
    total = rows * cols
    style_line = style.strip() or "统一的扁平插画风格，一套配色贯穿全部格子"

    lines = [
        f"一张{rows}行{cols}列、共 {total} 格的故事板预览图（storyboard / 分镜总览），整张竖版 3:4。",
        f"{total} 个格子大小完全相等，格与格之间用 2px 白色细线分隔，每格左上角有一个白色圆角小标签，写着页码数字。",
        f"整张图的画风、配色、光源方向必须完全统一：{style_line}。",
        "每格只画该镜头的构图骨架和主体剪影/色块，不追求细节，目的是让人一眼看清整套图的结构和节奏。",
        "",
        "各格内容：",
    ]
    for idx in range(1, total + 1):
        frame = next((f for f in frames if int(f.get("index", 0)) == idx), None)
        if not frame:
            lines.append(f"第 {idx} 格：留白空镜，只有一个浅灰色占位方块。")
            continue
        role = {"cover": "封面", "info": "内容", "closing": "收尾"}.get(
            str(frame.get("role", "")), "内容"
        )
        scene = str(frame.get("scene", "")).strip()
        text = str(frame.get("text_on_image", "")).strip()
        seg = f"第 {idx} 格（{role}）：{scene}"
        if text:
            seg += f"；图面上写中文「{text}」"
        lines.append(seg)

    lines += [
        "",
        "重要：严格保持格子数量与排布，不要合并、不要多画、不要跨格；每格的页码标签必须清晰可读。",
        "整张图是一张预览总览图，不是把内容排成一整页海报。",
    ]
    return "\n".join(lines)

