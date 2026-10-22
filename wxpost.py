#!/usr/bin/env python3
"""wxpost —— 公众号「贴图 / 小绿书」图片消息发布器。

给任意 Agent（Claude Code / opencode / Codex / WorkBuddy）一个确定性的发布内核：
Agent 负责写文案、出图；wxpost 负责校验、压图、传素材、建草稿、记记忆。

用法速查：
  wxpost init                       配凭证（微信 + 可选生图通道）
  wxpost check [--json]             体检，Agent 启动时先跑这个
  wxpost style [--account X]        打印该账号的风格档案（写文案前必读）
  wxpost scaffold --dir ./post      生成素材目录骨架
  wxpost gen --dir ./post           按 image_prompts 逐张生图
  wxpost publish --dir ./post       建草稿（--submit 可直接发布）
  wxpost draft list|delete          草稿管理
  wxpost profile show|set|learn     账号长期记忆
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config, extract, imagegen, imaging, llm, profile, wechat  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

POST_FILE = "post.json"
MAX_TITLE = 32
MAX_CONTENT = 20000
MAX_IMAGES = 20


# ---------------------------------------------------------------- 输出工具


def out(msg: str = "") -> None:
    print(msg, flush=True)


def err(msg: str) -> None:
    print(f"错误：{msg}", file=sys.stderr, flush=True)


def ok_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------- post.json


class PostError(Exception):
    pass


def post_path(folder: Path) -> Path:
    return folder / POST_FILE


def load_post(folder: Path) -> dict:
    p = post_path(folder)
    if not p.exists():
        raise PostError(f"找不到 {p}。先跑：wxpost scaffold --dir {folder}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PostError(f"{p} 不是合法 JSON：{exc}") from exc


def save_post(folder: Path, data: dict) -> None:
    post_path(folder).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


_STRIP_WARNED = False


def strip_markup(text: str) -> str:
    """图片消息的 content 只接受纯文本。把 Markdown/HTML 标记剥掉并告警。"""
    global _STRIP_WARNED
    original = text
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # 只吃「# 标题」这种带空格或独占一行的，别把 #话题 开头的井号吃掉
    text = re.sub(r"^\s{0,3}#{1,6}(?:\s+|$)", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)(\*|_)(.+?)\1(?!\w)", r"\2", text)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text != original.strip() and not _STRIP_WARNED:
        _STRIP_WARNED = True
        print("提示：已剥离正文里的 Markdown/HTML 标记（图片消息只收纯文本）")
    return text


def compose_content(post: dict) -> str:
    body = strip_markup(str(post.get("content", "")).strip())
    tags = [t.strip() for t in (post.get("hashtags") or []) if str(t).strip()]
    tags = [t if t.startswith("#") else f"#{t}" for t in tags]
    if tags:
        joined = " ".join(tags)
        if joined not in body:
            body = f"{body}\n\n{joined}" if body else joined
    return body


def validate(post: dict) -> list[str]:
    problems = []
    title = str(post.get("title", "")).strip()
    if not title:
        problems.append("title 为空（微信图片消息必填）")
    elif len(title) > MAX_TITLE:
        problems.append(f"title 有 {len(title)} 字，超过微信上限 {MAX_TITLE} 字，必须改短")
    content = compose_content(post)
    if not content:
        problems.append("content 为空（正文至少写一两句）")
    elif len(content) > MAX_CONTENT:
        problems.append(f"content 有 {len(content)} 字，超过 {MAX_CONTENT} 字上限")
    ratio = str(post.get("cover_ratio", "1_1"))
    if ratio not in ("1_1", "16_9", "2.35_1"):
        problems.append(f"cover_ratio='{ratio}' 不合法，只能是 1_1 / 16_9 / 2.35_1")
    return problems


def resolve_images(folder: Path, post: dict) -> list[Path]:
    listed = post.get("images") or []
    if listed:
        paths = []
        for item in listed:
            p = Path(item)
            paths.append(p if p.is_absolute() else (folder / p))
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise PostError(f"post.json 里列的图片不存在：{'、'.join(missing)}")
        return paths
    return imaging.collect_images(folder / "images")


def local_prepare(folder: Path, post: dict) -> list[dict]:
    ratio = post.get("image_ratio") or "3:4"
    prepared = imaging.prepare_batch(
        folder / "images", ratio=ratio, out_dir=folder / ".build"
    )
    if len(prepared) > MAX_IMAGES:
        raise PostError(
            f"图片有 {len(prepared)} 张，超过微信上限 {MAX_IMAGES} 张。删掉多余的再发（不会自动截断）"
        )
    return prepared


# ---------------------------------------------------------------- 子命令


def cmd_init(args) -> int:
    cfg = config.load()
    config.ensure_dir()

    if not args.image_only:
        account = args.account or cfg.get("active_account") or "default"
        app_id = args.app_id or (cfg["accounts"].get(account, {}) or {}).get("app_id", "")
        app_secret = args.app_secret or (cfg["accounts"].get(account, {}) or {}).get("app_secret", "")

        if not app_id:
            out("公众号后台 → 设置与开发 → 基本配置 → 开发者ID(AppID)")
            app_id = input("AppID: ").strip()
        if not app_secret:
            out("同页面下方「开发者密码(AppSecret)」，只显示一次")
            app_secret = input("AppSecret: ").strip()
        if not app_id or not app_secret:
            err("AppID / AppSecret 不能为空")
            return 2

        author = args.author
        if author is None:
            author = (cfg["accounts"].get(account, {}) or {}).get("author", "")
            if not author:
                author = input("作者名（可留空）: ").strip()

        cfg["accounts"][account] = {
            "app_id": app_id,
            "app_secret": app_secret,
            "author": author,
        }
        if not cfg.get("active_account"):
            cfg["active_account"] = account
        config.save(cfg)
        out(f"已保存账号 '{account}' 到 {config.CONFIG_PATH}")

        # 立刻验一次 token
        try:
            token = wechat.get_access_token(account, app_id, app_secret, force=True)
            out(f"凭证校验通过（access_token 前 8 位 {token[:8]}…）")
        except wechat.WeChatError as exc:
            err(f"凭证校验失败：{exc}")
            return 3

    # 生图 + 对话通道
    provider = args.provider
    if provider is None and not args.image_only:
        cur = (cfg.get("image") or {}).get("provider", "none")
        out("")
        out("生图 / 写文案通道（可跳过，之后随时用 wxpost init --image-only 再配）：")
        out("  1) 跳过 —— 文案和配图都由 Agent 自己搞（它能写也能调自带的生图）")
        out("  2) OpenAI 兼容中转站 —— 填 base_url + api_key，写文案和生图共用一个 key")
        choice = input(f"选一个 [{1 if cur == 'none' else 2}]: ").strip() or ("1" if cur == "none" else "2")
        provider = "none" if choice == "1" else "openai"

    if provider is not None:
        img = cfg.setdefault("image", {})
        llmcfg = cfg.setdefault("llm", {})
        if provider == "none":
            img["provider"] = "none"
            config.save(cfg)
            out("已跳过：文案由 Agent 写，配图由 Agent 出或用户手动放。")
        else:
            base_url = args.base_url or input("Base URL（如 https://api.xxx.com/v1）: ").strip()
            api_key = args.api_key or input("API Key: ").strip()
            model = args.model or input("生图模型名（如 gpt-image-1 / flux-dev）: ").strip()
            if not (base_url and api_key and model):
                err("base_url / api_key / 模型名三项都要填")
                return 2
            img.update(
                {
                    "provider": "openai",
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    "size": args.size or img.get("size", ""),
                }
            )
            llm_model = args.llm_model
            if llm_model is None:
                llm_model = input(
                    "对话模型名（用于自动写文案/配图方案，留空=不启用，Agent 自己写）: "
                ).strip()
            llmcfg.update(
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": llm_model,
                }
            )
            config.save(cfg)
            probe = imagegen.probe(cfg)
            out(f"生图通道：{'可用' if probe['ok'] else '待确认'} —— {probe['detail']}")
            if llm_model:
                out(f"写文案通道：{llm_model}（可跑 wxpost write 试）")
            else:
                out("写文案通道：未启用，文案交给 Agent 写")

    out("")
    out("配置完成。建议下一步：wxpost check")
    return 0


def cmd_check(args) -> int:
    cfg = config.load()
    result: dict = {"config": config.summary(cfg), "checks": []}

    accounts = cfg.get("accounts") or {}
    if not accounts:
        result["checks"].append({"name": "微信凭证", "ok": False, "detail": "还没配任何账号，跑 wxpost init"})
    else:
        for key, acct in accounts.items():
            try:
                wechat.get_access_token(key, acct["app_id"], acct["app_secret"])
                entry = {"name": f"微信凭证/{key}", "ok": True, "detail": "access_token 获取正常"}
                try:
                    if not args.no_api:
                        count = wechat.draft_count(
                            wechat.get_access_token(key, acct["app_id"], acct["app_secret"])
                        )
                        entry["detail"] += f"，草稿箱现有 {count} 条"
                except wechat.WeChatError as exc:
                    entry["detail"] += f"，但草稿接口不可用：{exc}"
                    entry["ok"] = False
                result["checks"].append(entry)
            except wechat.WeChatError as exc:
                result["checks"].append({"name": f"微信凭证/{key}", "ok": False, "detail": str(exc)})

    cap = imagegen.capability(cfg)
    result["image"] = cap
    result["checks"].append(
        {
            "name": "生图通道",
            "ok": cap["ready"],
            "detail": cap["reason"],
            "ask_user": cap.get("ask_user", False),
        }
    )

    lcap = llm.capability(cfg)
    result["llm"] = lcap
    result["checks"].append(
        {
            "name": "写文案通道（可选）",
            "ok": lcap["ready"],
            "detail": lcap["reason"],
            "optional": True,
        }
    )

    try:
        import PIL  # noqa: F401

        result["checks"].append({"name": "依赖 Pillow", "ok": True, "detail": "已安装"})
    except ImportError:
        result["checks"].append({"name": "依赖 Pillow", "ok": False, "detail": "缺 Pillow，图片无法压缩"})

    try:
        active = config.resolve_account(cfg)
        p = profile.load(active)
        result["profile"] = {
            "account": active,
            "exists": profile.exists(active),
            "notes": len(p.get("notes") or []),
            "published": (p.get("stats") or {}).get("published_count", 0),
        }
        result["checks"].append(
            {
                "name": f"账号记忆/{active}",
                "ok": True,
                "detail": f"笔记 {len(p.get('notes') or [])} 条，已发布 {(p.get('stats') or {}).get('published_count', 0)} 篇",
            }
        )
    except config.ConfigError as exc:
        result["checks"].append({"name": "账号记忆", "ok": False, "detail": str(exc)})

    optional = ("生图通道", "写文案通道（可选）")
    result["ready"] = all(
        c.get("ok") for c in result["checks"] if c["name"] not in optional
    )

    if args.json:
        ok_json(result)
    else:
        out(f"配置：{config.CONFIG_PATH}")
        for c in result["checks"]:
            flag = "OK  " if c["ok"] else ("skip" if c.get("optional") else "FAIL")
            out(f"  [{flag}] {c['name']}：{c['detail']}")
        if not cap["ready"]:
            out("")
            out("注意：生图通道不可用。按 SKILL 约定，必须先问用户：")
            out("  (A) 用你（Agent）自带的生图能力出图 → 图片放进 post/images/")
            out("  (B) 配一个中转站 → wxpost init --image-only，之后 wxpost write/design 能自动出文案和图")
        out("")
        out("可以发布本地图片" if result["ready"] else "还有问题没解决，见上面 FAIL 项")
    return 0 if result["ready"] else 1


def cmd_style(args) -> int:
    cfg = config.load()
    try:
        key = config.resolve_account(cfg, args.account)
    except config.ConfigError as exc:
        err(str(exc))
        return 2
    if args.json:
        ok_json(profile.load(key))
        return 0
    out(profile.to_prompt(key))
    if not profile.exists(key):
        out("")
        out(f"（账号 '{key}' 还没有独立记忆，上面是默认值。攒经验：wxpost profile learn \"以后封面统一用浅色底\"）")
    return 0


def cmd_scaffold(args) -> int:
    cfg = config.load()
    folder = Path(args.dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    try:
        key = config.resolve_account(cfg, args.account)
    except config.ConfigError:
        key = args.account or ""
    p = profile.load(key) if key else profile.load("default")

    template = {
        "account": key,
        "brief": "",
        "title": "",
        "content": "",
        "hashtags": [],
        "author": (cfg["accounts"].get(key, {}) or {}).get("author", "") if key else "",
        "cover_ratio": p["layout"].get("cover_ratio", "1_1"),
        "image_ratio": "3:4",
        "image_prompts": [],
        "images": [],
        "need_open_comment": cfg["defaults"].get("need_open_comment", 1),
        "only_fans_can_comment": cfg["defaults"].get("only_fans_can_comment", 0),
        "last_media_id": "",
        "status": "draft",
    }
    target = post_path(folder)
    if target.exists() and not args.force:
        out(f"{target} 已存在，保留原文件（加 --force 覆盖）")
    else:
        save_post(folder, template)
        out(f"已生成 {target}")

    out("")
    out(f"图片放这里：{folder / 'images'}（文件名 01.jpg、02.jpg… 排第一的张自动作封面）")
    out("")
    out("---- 写文案前先读这个 ----")
    out(profile.to_prompt(key) if key else "（未配置账号，按默认小红书风格写）")
    return 0


def _pick_account(cfg: dict, args, post: dict) -> str:
    try:
        return config.resolve_account(cfg, args.account or post.get("account"))
    except config.ConfigError:
        return ""


def _apply_plan(folder: Path, post: dict, data: dict, brief: str, count: int) -> None:
    title = str(data.get("title", "")).strip()
    if len(title) > MAX_TITLE:
        out(f"警告：模型给的标题 {len(title)} 字，超过微信 32 字上限，已截断。建议手动改一版更好的。")
        title = title[:MAX_TITLE]
    content = strip_markup(str(data.get("content", "")).strip())
    tags = [str(t).strip() for t in (data.get("hashtags") or []) if str(t).strip()]
    prompts = [str(p).strip() for p in (data.get("image_prompts") or []) if str(p).strip()]

    post["brief"] = brief
    if title:
        post["title"] = title
    if content:
        post["content"] = content
    if tags:
        post["hashtags"] = tags
    if prompts:
        post["image_prompts"] = prompts
    post["status"] = "planned"
    save_post(folder, post)

    out("")
    if title:
        out(f"标题（{len(title)} 字）：{title}")
    if content:
        out(f"正文（{len(content)} 字）：")
        for line in content.splitlines():
            out(f"  {line}" if line.strip() else "")
    if tags:
        out(f"标签：{' '.join(tags)}")
    if prompts:
        out(f"配图方案（{len(prompts)} 张，第 1 张自动作封面）：")
        for idx, p in enumerate(prompts, start=1):
            out(f"  {idx:02d}. {p[:110]}{'…' if len(p) > 110 else ''}")
    out("")
    out(f"已写入 {post_path(folder)}")


def cmd_write(args) -> int:
    cfg = config.load()
    folder = Path(args.dir).resolve()
    post = load_post(folder)
    brief = args.brief or post.get("brief") or ""
    if not brief:
        err('没给选题。加 --brief "一句话说明要做什么内容"')
        return 2

    cap = llm.capability(cfg)
    if not cap["ready"]:
        err(f"自动写文案不可用：{cap['reason']}")
        out("→ 改由你自己写（你本来就是模型）：按 post.json 契约填好 title/content/hashtags/image_prompts，")
        out("  再跑 wxpost gen 出图、wxpost publish 发草稿。")
        return 3

    key = _pick_account(cfg, args, post)
    style = profile.to_prompt(key) if key else ""
    count = args.count or (
        int((profile.load(key)["layout"] or {}).get("image_count", 4)) if key else 4
    )
    out(f"正在生成：{brief}（{count} 张配图）…")
    try:
        raw = llm.chat(cfg, llm.build_writer_messages(style, brief, count, args.extra or ""))
        data = llm.extract_json(raw)
    except llm.LLMError as exc:
        err(str(exc))
        return 4

    _apply_plan(folder, post, data, brief, count)
    out(f"下一步：wxpost gen --dir {folder}")
    return 0


def cmd_design(args) -> int:
    cfg = config.load()
    folder = Path(args.dir).resolve()
    post = load_post(folder)
    brief = args.brief or post.get("brief") or str(post.get("title", ""))
    if not brief:
        err('没给主题。加 --brief "这篇讲什么"')
        return 2

    cap = llm.capability(cfg)
    if not cap["ready"]:
        err(f"自动配图策划不可用：{cap['reason']}")
        out("→ 改由你自己设计：把每张图的完整提示词写进 post.json 的 image_prompts，再跑 wxpost gen。")
        return 3

    key = _pick_account(cfg, args, post)
    style = profile.to_prompt(key) if key else ""
    count = args.count or (
        int((profile.load(key)["layout"] or {}).get("image_count", 4)) if key else 4
    )
    out(f"正在设计配图：{brief}（{count} 张）…")
    try:
        raw = llm.chat(
            cfg,
            llm.build_image_plan_messages(style, brief, count, str(post.get("content", ""))),
        )
        data = llm.extract_json(raw)
    except llm.LLMError as exc:
        err(str(exc))
        return 4

    prompts = [str(p).strip() for p in (data.get("image_prompts") or []) if str(p).strip()]
    if not prompts:
        err("模型没给出 image_prompts，重跑一次或改用 wxpost write")
        return 4
    post["image_prompts"] = prompts
    post["brief"] = brief
    save_post(folder, post)

    out("")
    out(f"配图方案（{len(prompts)} 张，第 1 张自动作封面）：")
    for idx, p in enumerate(prompts, start=1):
        out(f"  {idx:02d}. {p[:110]}{'…' if len(p) > 110 else ''}")
    out("")
    out(f"已写入 {post_path(folder)}")
    out(f"下一步：wxpost gen --dir {folder}")
    return 0


def cmd_storyboard(args) -> int:
    """先出一张九宫格故事板，确认整体结构后再逐张出正式图。"""
    cfg = config.load()
    folder = Path(args.dir).resolve()
    post = load_post(folder)
    key = _pick_account(cfg, args, post)
    prompts = post.get("image_prompts") or []
    brief = args.brief or post.get("brief") or str(post.get("title", ""))
    if not brief:
        if args.reuse_prompts and prompts:
            brief = "（沿用已有配图提示词）"
        else:
            err('没给主题。加 --brief "这篇讲什么"')
            return 2

    count = args.count or len(prompts) or (
        int((profile.load(key)["layout"] or {}).get("image_count", 4)) if key else 4
    )
    count = max(1, min(int(count), MAX_IMAGES))

    frames: list[dict] = []
    lcap = llm.capability(cfg)
    if lcap["ready"] and not args.reuse_prompts:
        out(f"正在拆解分镜：{brief}（{count} 格）…")
        style = profile.to_prompt(key) if key else ""
        try:
            raw = llm.chat(
                cfg,
                llm.build_storyboard_messages(
                    style, brief, count, str(post.get("content", ""))
                ),
            )
            data = llm.extract_json(raw)
            frames = [f for f in (data.get("frames") or []) if isinstance(f, dict)]
        except llm.LLMError as exc:
            out(f"分镜拆解失败：{exc}")
            out("改用 post.json 里现有的 image_prompts 兜底")

    if not frames:
        if not prompts:
            err(
                "拆不出分镜：没有可用的对话模型，post.json 里也没有 image_prompts。\n"
                "→ 先跑 wxpost write/design，或自己把每张图的提示词写进 image_prompts，再回来出故事板"
            )
            return 2
        frames = []
        for idx, prompt in enumerate(prompts[:count], start=1):
            role = "cover" if idx == 1 else ("closing" if idx == len(prompts) else "info")
            # 剥掉「封面：」「内容图1：」这类前缀，当分镜标题用
            cleaned = re.sub(r"^[^：:]{1,8}[：:]\s*", "", prompt.strip()) or prompt.strip()
            frames.append(
                {
                    "index": idx,
                    "role": role,
                    "headline": cleaned[:14],
                    "scene": cleaned[:80],
                    "text_on_image": "",
                }
            )
        out("（未调用对话模型，直接用现有 image_prompts 当分镜）")

    for idx, frame in enumerate(frames, start=1):
        frame["index"] = idx
        frame.setdefault("role", "cover" if idx == 1 else ("closing" if idx == len(frames) else "info"))

    st_dir = folder / "storyboard"
    st_dir.mkdir(parents=True, exist_ok=True)
    (st_dir / "storyboard.json").write_text(
        json.dumps({"brief": brief, "count": len(frames), "frames": frames}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    out("")
    out(f"分镜表（{len(frames)} 格）：")
    role_cn = {"cover": "封面", "info": "内容", "closing": "收尾"}
    for frame in frames:
        idx = frame["index"]
        role = role_cn.get(str(frame.get("role")), "内容")
        head = str(frame.get("headline", "")).strip()
        text = str(frame.get("text_on_image", "")).strip()
        line = f"  {idx:02d}. [{role}] {head}"
        if text:
            line += f"　图上字：「{text}」"
        out(line)
    out(f"\n已保存分镜表：{st_dir / 'storyboard.json'}")

    if args.no_image:
        out("（--no-image：跳过故事板出图，分镜表已就绪）")
        return 0

    cap = imagegen.capability(cfg)
    if not cap["ready"]:
        err(f"故事板出图不可用：{cap['reason']}\n{cap.get('ask_hint', '')}")
        out("→ 分镜表已经写好了，你可以让 Agent 按分镜表自己出图，或配好通道后重跑本命令")
        return 3

    style = ""
    if key:
        style = (profile.load(key)["layout"] or {}).get("image_style", "")
    prompt = llm.compose_storyboard_prompt(frames, style, len(frames))
    (st_dir / "storyboard_prompt.txt").write_text(prompt, encoding="utf-8")

    target = st_dir / "storyboard.png"
    out("")
    out("正在生成九宫格故事板…")
    try:
        imagegen.generate(cfg, prompt, target, ratio="3:4")
    except imagegen.ImageGenError as exc:
        err(f"故事板出图失败：{exc}")
        return 4
    out(f"故事板：{target}")
    out("")
    out("先看这张图和上面的分镜表，确认结构和风格没问题，再跑：")
    out(f"  wxpost gen --dir {folder}")
    out("要调整分镜就改 storyboard/storyboard.json 或直接改 post.json 的 image_prompts 后重跑。")
    return 0


def cmd_gen(args) -> int:
    cfg = config.load()
    folder = Path(args.dir).resolve()
    post = load_post(folder)
    prompts = post.get("image_prompts") or []
    if not prompts:
        err("post.json 里没有 image_prompts。先把每张图的提示词写进去，或用 Agent 自带的生图能力出图后放进 images/")
        return 2

    cap = imagegen.capability(cfg)
    if not cap["ready"]:
        err(f"{cap['reason']}\n{cap.get('ask_hint','')}")
        return 3

    ratio = post.get("image_ratio") or "3:4"
    style = ""
    try:
        key = config.resolve_account(cfg, args.account)
        style = (profile.load(key)["layout"] or {}).get("image_style", "")
    except config.ConfigError:
        pass

    done = 0
    for idx, prompt in enumerate(prompts, start=1):
        target = folder / "images" / f"{idx:02d}.png"
        if target.exists() and not args.force:
            out(f"跳过 {target.name}（已存在，--force 覆盖）")
            continue
        full = f"{prompt}。{style}" if style else prompt
        out(f"[{idx}/{len(prompts)}] 生成 {target.name} …")
        try:
            imagegen.generate(cfg, full, target, ratio=ratio)
        except imagegen.ImageGenError as exc:
            err(f"第 {idx} 张失败：{exc}")
            return 4
        done += 1

    out(f"完成，新生成 {done} 张。下一步：wxpost publish --dir {folder}")
    return 0


def cmd_publish(args) -> int:
    cfg = config.load()
    folder = Path(args.dir).resolve()
    post = load_post(folder)

    problems = validate(post)
    if problems:
        err("文案不合规，先改：\n  - " + "\n  - ".join(problems))
        return 2

    try:
        key, acct = config.get_account(cfg, args.account or post.get("account"))
    except config.ConfigError as exc:
        err(str(exc))
        return 2

    content = compose_content(post)
    prepared = local_prepare(folder, post)
    first = prepared[0]
    digest = hashlib.sha256((post.get("title", "") + content).encode("utf-8")).hexdigest()[:16]

    out(f"账号：{key}")
    out(f"标题：{post['title']}（{len(post['title'])} 字）")
    out(f"正文：{len(content)} 字，{len(prepared)} 张图，封面比例 {post.get('cover_ratio','1_1')}")
    for item in prepared:
        out(f"  - {item['name']}  {item['width']}x{item['height']}  {item['bytes']/1024:.0f} KB")

    if args.dry_run:
        out("")
        out("（--dry-run：本地校验全部通过，未上传、未建草稿）")
        return 0

    # 防重复：内容没变且上次已建过草稿
    prior = post.get("last_media_id") or ""
    if prior and not args.allow_duplicate:
        same = post.get("last_digest") == digest
        msg = (
            f"这篇上次已经建过草稿（media_id {prior}）"
            + ("，内容没变。" if same else "，内容有改动。")
            + "继续会再建一条新草稿，草稿箱会重复。"
        )
        out(msg)
        if not confirm("仍要新建一条？", args.yes):
            out("已取消。若要覆盖旧草稿，先跑：wxpost draft delete " + prior)
            return 0

    token = wechat.get_access_token(key, acct["app_id"], acct["app_secret"])
    uploaded = []
    try:
        for idx, item in enumerate(prepared, start=1):
            out(f"上传素材 [{idx}/{len(prepared)}] {item['name']} …")
            res = wechat.upload_permanent_image(token, Path(item["path"]))
            uploaded.append(
                {
                    "media_id": res["media_id"],
                    "width": item["width"],
                    "height": item["height"],
                }
            )
        media_id = wechat.add_newspic_draft(
            token,
            title=post["title"],
            content=content,
            image_items=uploaded,
            cover_ratio=post.get("cover_ratio", "1_1"),
            author=post.get("author", ""),
            need_open_comment=post.get("need_open_comment", 1),
            only_fans_can_comment=post.get("only_fans_can_comment", 0),
        )
    except wechat.WeChatError as exc:
        err(str(exc))
        return 5

    post["last_media_id"] = media_id
    post["last_digest"] = digest
    post["status"] = "drafted"
    save_post(folder, post)
    profile.record_publish(key, post["title"], media_id)

    out("")
    out(f"草稿已创建：media_id = {media_id}")
    out("去公众号后台「内容与互动 → 草稿箱」就能看到，手机预览确认后再发。")

    if args.submit:
        out("")
        out("警告：提交发布后不可撤销，会直接推送给粉丝。")
        if not confirm("确认现在发布？", args.yes):
            out("已停在草稿状态。")
            return 0
        try:
            publish_id = wechat.submit_publish(token, media_id)
        except wechat.WeChatError as exc:
            err(str(exc))
            return 6
        out(f"已提交发布，publish_id = {publish_id}")
        out("发布是异步的，可用公众号后台确认结果。")
        post["status"] = "published"
        save_post(folder, post)
    return 0


def cmd_draft(args) -> int:
    cfg = config.load()
    try:
        key, acct = config.get_account(cfg, args.account)
    except config.ConfigError as exc:
        err(str(exc))
        return 2
    token = wechat.get_access_token(key, acct["app_id"], acct["app_secret"])
    try:
        if args.action == "list":
            items = wechat.draft_list(token, count=args.count)
            if not items:
                out("草稿箱是空的")
                return 0
            out(f"草稿箱（账号 {key}）：")
            for it in items:
                out(f"  {it['media_id']}  [{it['article_type']}]  {it['title']}")
            return 0
        if args.action == "delete":
            if not args.media_id:
                err("要删哪条？wxpost draft delete <media_id>")
                return 2
            wechat.draft_delete(token, args.media_id)
            out(f"已删除草稿 {args.media_id}")
            return 0
    except wechat.WeChatError as exc:
        err(str(exc))
        return 5
    return 2


def cmd_profile(args) -> int:
    cfg = config.load()
    if args.action == "list":
        keys = profile.list_accounts()
        if not keys:
            out("还没有任何账号记忆")
            return 0
        for k in keys:
            p = profile.load(k)
            notes = len(p.get("notes") or [])
            pub = (p.get("stats") or {}).get("published_count", 0)
            out(f"  {k}  笔记 {notes} 条  已发布 {pub} 篇")
        return 0

    if args.action == "fields":
        out("style 分组：")
        for k, v in profile.DEFAULT_STYLE.items():
            out(f"  style.{k}  默认 {json.dumps(v, ensure_ascii=False)}")
        out("layout 分组：")
        for k, v in profile.DEFAULT_LAYOUT.items():
            out(f"  layout.{k}  默认 {json.dumps(v, ensure_ascii=False)}")
        out("rules 分组：")
        for k, v in profile.DEFAULT_RULES.items():
            out(f"  rules.{k}  默认 {json.dumps(v, ensure_ascii=False)}")
        return 0

    try:
        key = config.resolve_account(cfg, args.account)
    except config.ConfigError as exc:
        err(str(exc))
        return 2

    if args.action == "show":
        if args.json:
            ok_json(profile.load(key))
        else:
            out(profile.to_prompt(key))
        return 0

    if args.action == "set":
        if not args.field or args.value is None:
            err('用法：wxpost profile set style.tone "更犀利一点"')
            return 2
        ok, msg = profile.set_field(key, args.field, args.value)
        out(msg) if ok else err(msg)
        return 0 if ok else 2

    if args.action == "learn":
        note = args.note or args.field
        if not note:
            err('用法：wxpost profile learn "以后封面统一用浅蓝底 + 大字标题"')
            return 2
        profile.add_note(key, note)
        out(f"已记到账号 '{key}' 的长期记忆。以后每次写文案都会带上这条。")
        return 0

    return 2


def cmd_from_md(args) -> int:
    src = Path(args.file).resolve()
    if not src.exists():
        err(f"文件不存在：{src}")
        return 2
    text = src.read_text(encoding="utf-8")
    folder = Path(args.dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    title = ""
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if m:
        title = m.group(1).strip()
        text = text.replace(m.group(0), "", 1)

    tags = re.findall(r"#[^\s#，。！？、]+", text)
    seen, hashtags = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            hashtags.append(t)
    for t in hashtags:
        text = text.replace(t, "")

    cfg = config.load()
    try:
        key = config.resolve_account(cfg, args.account)
    except config.ConfigError:
        key = ""

    post = {
        "account": key,
        "title": title or src.stem,
        "content": strip_markup(text),
        "hashtags": hashtags,
        "author": (cfg["accounts"].get(key, {}) or {}).get("author", "") if key else "",
        "cover_ratio": args.cover_ratio,
        "image_ratio": "3:4",
        "image_prompts": [],
        "images": [],
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
        "last_media_id": "",
        "status": "draft",
    }
    save_post(folder, post)
    out(f"已生成 {post_path(folder)}")
    out(f"标题：{post['title']}")
    out(f"正文：{len(post['content'])} 字，标签 {len(hashtags)} 个")
    out(f"还需要：把图片放进 {folder / 'images'}")
    problems = validate(post)
    if problems:
        out("")
        out("待修正：")
        for p in problems:
            out(f"  - {p}")
    return 0


def cmd_from_url(args) -> int:
    """抓一条现成的图文链接（公众号/微信贴图/小红书），拆成素材。

    默认是「参考模式」：存成 reference/ 素材，交给后续重写；
    --copy 才是「搬运模式」：直接填进 post.json 当成品。
    """
    folder = Path(args.dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    out(f"正在抓取：{args.url}")
    try:
        info = extract.extract(args.url)
    except extract.ExtractError as exc:
        err(str(exc))
        return 4

    kind_cn = {"article": "公众号文章", "picture": "微信贴图", "note": "小红书笔记"}.get(
        info.get("kind", ""), info.get("kind", "未知")
    )
    title = str(info.get("title", "")).strip()
    content = str(info.get("content", "")).strip()
    images = info.get("images") or []

    out("")
    out(f"识别为：{kind_cn}")
    out(f"来源：{info.get('source_url', args.url)}")
    out(f"标题：{title or '（无）'}{f'（{len(title)} 字）' if title else ''}")
    out(f"作者：{info.get('author') or '（无）'}")
    out(f"正文：{len(content)} 字｜图片：{len(images)} 张")

    ref_dir = folder / "reference"
    ref_dir.mkdir(exist_ok=True)
    for stale in list(ref_dir.glob("*.jpg")) + list(ref_dir.glob("*.png")) + list(ref_dir.glob("*.webp")):
        stale.unlink()

    saved: list[Path] = []
    if images:
        out("")
        out("下载图片…")
        for idx, url in enumerate(images[: args.max_images], start=1):
            try:
                p = extract.download_image(url, ref_dir / f"{idx:02d}", referer=info.get("source_url", ""))
                saved.append(p)
                out(f"  {idx:02d} → {p.name}（{p.stat().st_size / 1024:.0f} KB）")
            except extract.ExtractError as exc:
                out(f"  {idx:02d} 跳过：{exc}")

    md_lines = [
        f"# 参考素材：{title or '（无标题）'}",
        "",
        f"- 来源：{info.get('source_url', args.url)}",
        f"- 类型：{kind_cn}",
        f"- 作者：{info.get('author') or '未知'}",
        f"- 抓取时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 图片：{len(saved)} 张（同目录）",
        "",
        "## 原文正文",
        "",
        content or "（无正文）",
        "",
        "## 原文图片",
        "",
    ]
    if saved:
        md_lines += [f"{idx:02d}. {p.name}" for idx, p in enumerate(saved, start=1)]
    else:
        md_lines.append("（没有抓到图片）")
    md_lines.append("")
    (ref_dir / "reference.md").write_text("\n".join(md_lines), encoding="utf-8")
    out("")
    out(f"参考素材已存：{ref_dir / 'reference.md'}")

    post = load_post(folder) if post_path(folder).exists() else None
    if post is None:
        cfg = config.load()
        key = ""
        try:
            key = config.resolve_account(cfg, args.account)
        except config.ConfigError:
            pass
        post = {
            "account": key,
            "brief": "",
            "title": "",
            "content": "",
            "hashtags": [],
            "author": (cfg["accounts"].get(key, {}) or {}).get("author", "") if key else "",
            "cover_ratio": "1_1",
            "image_ratio": "3:4",
            "image_prompts": [],
            "images": [],
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
            "last_media_id": "",
            "status": "draft",
        }

    if args.copy:
        # 搬运模式：原样复用内容
        if title:
            post["title"] = title[:MAX_TITLE]
            if len(title) > MAX_TITLE:
                out(f"注意：原标题 {len(title)} 字，已截断到 {MAX_TITLE} 字，建议手动改一版")
        post["content"] = content
        post["brief"] = title or content[:60]
        if saved:
            img_dir = folder / "images"
            for old in img_dir.iterdir():
                if old.is_file() and old.is_symlink() is False:
                    old.unlink()
            for idx, p in enumerate(saved, start=1):
                target = img_dir / f"{idx:02d}{p.suffix}"
                target.write_bytes(p.read_bytes())
            post["images"] = []
        save_post(folder, post)
        out("")
        out(f"已按原文填好 {post_path(folder)}（搬运模式）")
        problems = validate(post)
        if problems:
            out("待修正：")
            for p in problems:
                out(f"  - {p}")
        out(f"下一步：wxpost publish --dir {folder} --dry-run")
        return 0

    # 参考模式：只填 brief，内容交给后续重写
    post["brief"] = title or content[:60]
    if not post.get("account"):
        post["account"] = args.account or ""
    save_post(folder, post)
    out("")
    out(f"已把选题写进 post.json 的 brief：{post['brief']}")
    out("")
    out("下一步（参考模式，不直接搬原文）：")
    out(f"  1) wxpost write --dir {folder} --count {args.max_images}   # 按参考素材重写")
    out(f"  2) wxpost storyboard --dir {folder}                        # 出九宫格确认结构")
    out(f"  3) wxpost gen --dir {folder}                               # 逐张出图")
    out(f"  4) wxpost publish --dir {folder}")
    out("")
    out(f"想直接复用原文和图片：wxpost from-url <url> --dir {folder} --copy")
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wxpost",
        description="公众号贴图（小绿书 / 图片消息）发布器：文案由 Agent 写，发布交给这里",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="典型流程：wxpost check → wxpost style → 写 post.json → wxpost gen → wxpost publish",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="配置凭证（微信 + 可选生图通道）")
    s.add_argument("--account", help="账号标识，如 diandongtong")
    s.add_argument("--app-id")
    s.add_argument("--app-secret")
    s.add_argument("--author")
    s.add_argument("--image-only", action="store_true", help="只配生图通道")
    s.add_argument("--provider", choices=["none", "openai"], help="生图 provider")
    s.add_argument("--base-url")
    s.add_argument("--api-key")
    s.add_argument("--model")
    s.add_argument("--size")
    s.add_argument("--llm-model", help="对话模型名，用于自动写文案；留空则文案交给 Agent")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("check", help="体检：凭证、生图、依赖、记忆")
    s.add_argument("--json", action="store_true", help="输出结构化 JSON 给 Agent 解析")
    s.add_argument("--no-api", action="store_true", help="跳过草稿接口调用")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("style", help="打印账号风格档案（写文案前必读）")
    s.add_argument("--account")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_style)

    s = sub.add_parser("scaffold", help="生成素材目录骨架")
    s.add_argument("--dir", required=True)
    s.add_argument("--account")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_scaffold)

    s = sub.add_parser("write", help="一句话生成标题+正文+标签+配图方案（需配对话模型）")
    s.add_argument("--dir", required=True)
    s.add_argument("--brief", help='选题，如 "讲讲用 Claude Code 三个月踩的坑"')
    s.add_argument("--count", type=int, help="配图数量，默认取账号记忆里的值")
    s.add_argument("--extra", help="额外要求，如「语气更犀利」「别提到具体产品名」")
    s.add_argument("--account")
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("design", help="只设计配图方案（已有文案，只缺图）")
    s.add_argument("--dir", required=True)
    s.add_argument("--brief", help="主题，默认取 post.json 的 brief 或 title")
    s.add_argument("--count", type=int)
    s.add_argument("--account")
    s.set_defaults(func=cmd_design)

    s = sub.add_parser("storyboard", help="先出九宫格故事板，确认结构后再逐张出图")
    s.add_argument("--dir", required=True)
    s.add_argument("--brief")
    s.add_argument("--count", type=int)
    s.add_argument("--account")
    s.add_argument("--reuse-prompts", action="store_true", help="不调对话模型，直接用现有 image_prompts 当分镜")
    s.add_argument("--no-image", action="store_true", help="只出分镜表，不生故事板图")
    s.set_defaults(func=cmd_storyboard)

    s = sub.add_parser("gen", help="按 image_prompts 逐张生图")
    s.add_argument("--dir", required=True)
    s.add_argument("--account")
    s.add_argument("--force", action="store_true", help="覆盖已存在的图")
    s.set_defaults(func=cmd_gen)

    s = sub.add_parser("publish", help="压图 → 传素材 → 建草稿")
    s.add_argument("--dir", required=True)
    s.add_argument("--account")
    s.add_argument("--submit", action="store_true", help="建完草稿直接提交发布（不可撤销）")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", "-y", action="store_true", help="跳过交互确认")
    s.add_argument("--allow-duplicate", action="store_true", help="允许重复建草稿")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("draft", help="草稿管理")
    s.add_argument("action", choices=["list", "delete"])
    s.add_argument("media_id", nargs="?")
    s.add_argument("--account")
    s.add_argument("--count", type=int, default=20)
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("profile", help="账号长期记忆")
    s.add_argument("action", choices=["show", "set", "learn", "fields", "list"])
    s.add_argument("field", nargs="?", help="如 style.tone")
    s.add_argument("value", nargs="?", help="新值")
    s.add_argument("--note", help="learn 用：要记住的话")
    s.add_argument("--account")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("from-url", help="抓公众号/微信贴图/小红书链接，拆成参考素材")
    s.add_argument("url")
    s.add_argument("--dir", required=True)
    s.add_argument("--account")
    s.add_argument("--max-images", type=int, default=9, help="最多抓几张图，默认 9")
    s.add_argument("--copy", action="store_true", help="搬运模式：原文原图直接填进 post.json")
    s.set_defaults(func=cmd_from_url)

    s = sub.add_parser("from-md", help="把 Markdown 笔记转成 post 目录")
    s.add_argument("file")
    s.add_argument("--dir", required=True)
    s.add_argument("--account")
    s.add_argument("--cover-ratio", default="1_1", choices=["1_1", "16_9", "2.35_1"])
    s.set_defaults(func=cmd_from_md)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, PostError) as exc:
        err(str(exc))
        return 2
    except imaging.ImageError as exc:
        err(str(exc))
        return 4
    except KeyboardInterrupt:
        out("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
