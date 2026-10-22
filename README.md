<p align="center">
  <img src="assets/logo.png" width="110" alt="wxpost">
</p>

<h1 align="center">wxpost</h1>

<p align="center"><b>一句话，变成一条能发的小绿书。</b></p>

<p align="center">
  写文案 → 设计配图 → 九宫格故事板 → 逐张生图 → 压图换比例 → 传素材 → 建草稿，一条链跑完。<br>
  任何能跑命令的 Agent 都能用：Claude Code · Codex · opencode · WorkBuddy
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Agent%20Skills-Standard-green" alt="Agent Skills">
  <img src="https://img.shields.io/badge/No%20MCP-Just%20CLI-orange" alt="No MCP">
</p>

<p align="center">
  <a href="#效果示例">效果</a> ·
  <a href="#安装">安装</a> ·
  <a href="#用法">用法</a> ·
  <a href="#它到底做了什么">它做了什么</a> ·
  <a href="#配图不是随便配的">配图规范</a> ·
  <a href="#诚实边界">诚实边界</a>
</p>

---

公众号的「贴图」就是微信里的小红书——`article_type=newspic` 图片消息：标题 + 多图轮播 + 下方一段纯文本正文 + #标签。

这个工具负责从「用户随口说的一句话」到「草稿箱里躺着的一篇成品」之间的**全部脏活**。你不用记 API、不用手动压图换比例、不用惦记上次那个号喜欢什么调性。

## 效果示例

```
你 ❯ 用「番禺新增 48 个电动车抓拍点」发个贴图，9 张图

它 ❯ 先看一下这个号的风格记忆：
      语气：口语化，像跟朋友聊天
      配图：扁平卡通插画，蓝黄撞色，粗黑描边
      禁用词：赋能、抓手、闭环

      文案和分镜已经出好，9 格：
      01 [封面] 番禺新增 48 个抓拍点   图上字「48个抓拍点」
      02 [内容] 事件时间线             图上字「11月21日启用」
      03 [内容] 五类违法行为           图上字「都会拍」
      04 [内容] 15 个桥隧点位          图上字「15个」
      …

      九宫格故事板：post/storyboard/storyboard.png
      结构和画风对不对？要改哪一格？

你 ❯ 第 5 格换成条形图，别的可以

它 ❯ 改好了，开始逐张出图
      [1/9] 01.jpg …  [9/9] 09.jpg

      已进草稿箱，media_id = xxxxxxxxxxxx
      去公众号后台「内容与互动 → 草稿箱」预览确认。
```

关键在于中间那次**停顿**：先出一张九宫格故事板，让你看见 9 张图的结构、节奏、画风是否统一，确认了再逐张放大。省掉「出完 9 张才发现方向全错」的浪费。

## 安装

### 方式一：一行命令

```bash
git clone https://github.com/whishi47/wechat-sticker.git ~/.wxpost && bash ~/.wxpost/install.sh
```

`install.sh` 会建虚拟环境、装依赖，并把 `SKILL.md` 部署到你机器上各个 Agent 的 skills 目录。

### 方式二：手动

```bash
git clone https://github.com/whishi47/wechat-sticker.git ~/.wxpost
cd ~/.wxpost
python3 -m venv .venv
.venv/bin/pip install requests Pillow        # Windows: .venv\Scripts\pip install requests Pillow
```

然后把 `SKILL.md` 放进你客户端的 skills 目录：

| 客户端 | 路径 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/wxpost/SKILL.md` |
| Claude Code | `~/.claude/skills/wxpost/SKILL.md` |
| opencode | `~/.config/opencode/skills/wxpost/SKILL.md` |
| Codex / 其他 | 内容追加到项目根 `AGENTS.md` 即可 |

### 方式三：只把 SKILL.md 当参考

不装也能读。`SKILL.md` 本身就是一份完整的操作手册，抄进任何 Agent 的上下文里都能照着干活。

## 用法

配一次凭证，之后都是说话：

```bash
~/.wxpost/wxpost.sh init       # 公众号 AppID/Secret + 可选的中转站 key
~/.wxpost/wxpost.sh check      # 体检，不通会告诉你缺什么
```

然后直接对 Agent 说：

> 发个贴图，讲讲这周电动车新规，6 张图

> 这篇小红书稿子转成贴图发草稿箱

> 先出个分镜我看看，别急着生图

> 以后封面都用浅蓝底加大字，别用实拍的

最后那句话会被记进这个公众号的长期档案，之后每篇都自动带上。

## 它到底做了什么

| 步骤 | 命令 | 说明 |
|---|---|---|
| 1. 读风格记忆 | `style` | 这个号喜欢什么调性、几张图、哪些词不能碰 |
| 2. 写文案 | `write` | 标题 / 正文 / #标签，一次出齐 |
| 3. 设计配图 | 同上 | 封面、内容图、收尾图各司其职，不是复制三张同样的图 |
| 4. 出故事板 | `storyboard` | 九宫格预览 + 分镜表，确认结构再往下 |
| 5. 逐张生图 | `gen` | 调生图模型，自动裁 3:4、压到 1.8MB 内 |
| 6. 建草稿 | `publish` | 传永久素材 → `draft/add` → 草稿箱 |
| 7. 记经验 | `profile learn` | 这次学到的偏好写进账号档案 |

每一步都能单独跑，也能只跑其中一段。文由 Agent 写、图由模型出、发布交给 CLI——**分工清晰，不需要给 CLI 再配一个大模型 key**。

## 配图不是随便配的

大多数图文工具把「配图」理解成「按张数生成 N 张图」。结果是一篇文章里三张图长着同一张脸，翻页像卡带。

这里强制按角色分工：

| 位置 | 角色 | 要什么 |
|---|---|---|
| 第 1 张（自动作封面） | 抢点击 | 强视觉冲击 + 图上直接放大字标题，主体明确，给文字留白 |
| 中间几张 | 讲信息 | 每张只承担一个信息点，形态轮换：信息图 / 对比 / 步骤 / 场景 / 地图 |
| 最后 1 张 | 收情绪 | 金句或行动引导，留白多，安静 |

外加几条硬约束，全部写死在生成提示词里：

- 全系列统一配色、同一画风（插画或摄影二选一）、同一光源方向
- 图上中文 5–12 字，逐字写进提示词，防模型自由发挥出乱码
- 不把正文原句照搬上图，提炼成关键词句
- 禁止真实品牌 logo、可辨认的真人肖像、复杂表格

**九宫格故事板**就是这套规范的验收工具——一张图看清 9 个分镜的结构和风格是否统一，不满意当场改，不用等出完 9 张。

## 每个号有自己的记性

同一个运营手里往往好几个号，一个偏专业、一个偏活泼，配图风格也完全不同。共用一个 prompt 是行不通的。

每个公众号一份独立档案，存在 `~/.wxpost/profiles/<账号>/profile.json`：

- **风格**：语气、人称、句式、emoji 密度、标题风格、正文长度、标签数量
- **配图**：张数、封面比例、统一视觉风格提示词
- **规矩**：禁用词、惯用词、偏爱/避开选题
- **经验**：自然语言笔记 + 已发布统计

攒经验有两条路：

```bash
wxpost profile learn "标题别用问句，显得软"        # 自然语言，优先级最高
wxpost profile set style.emoji heavy              # 落到具体字段，直接影响生成
```

`profile learn` 记的话会在之后每次写文案时被注入提示词，优先级高于默认值。

## 工作原理

1. **体检** — 先查凭证、生图通道、对话模型、依赖是否齐，缺什么直接说人话，不让你跑到一半才失败。
2. **取风格** — 把账号档案渲染成一段「写作要求」注入提示词，产出天然符合这个号的口味。
3. **拆分镜** — 把内容拆成 N 格，标注每格的角色、画面白描、图上文字。
4. **出故事板** — 拼成九宫格提示词，生成一张预览图供人确认。
5. **逐张出图** — 按分镜逐张生成，自动按 3:4 居中裁切、压到 1.8MB 以内。
6. **发布** — 上传永久素材拿 `media_id`，用 `newspic` 结构建草稿；`--submit` 才真正推给粉丝。

## 仓库结构

```
wxpost/
├── SKILL.md               Agent 接入层：教它什么时候调、怎么传参、报错怎么修
├── wxpost.py              主命令
├── wxpost.sh / .cmd       启动器（自动找 .venv）
├── lib/
│   ├── config.py          多账号配置（0600 权限，密钥不进日志）
│   ├── wechat.py          微信 API + 20 个错误码的人话翻译
│   ├── imaging.py         3:4 居中裁切 + 逐级压缩
│   ├── imagegen.py        生图适配（OpenAI 兼容 /v1/images/generations）
│   ├── llm.py             文案生成 + 分镜策划 + 九宫格提示词拼装
│   └── profile.py         账号长期记忆
├── assets/                logo 与插图
├── config.example.json    配置样例
└── install.sh             一键部署到各客户端
```

素材目录长这样：

```
post/
├── post.json             标题/正文/标签/比例/配图提示词
├── images/               原图，01.jpg 起，第一张即封面（不会被改动）
├── storyboard/           九宫格预览 + 分镜表
└── .build/               处理后的成品，自动生成
```

## 诚实边界

**一个不告诉你局限在哪的工具，不值得用。** 这些地方它做不到：

- **只建草稿，不群发。** 想直接推给粉丝要加 `--submit`，且不可撤销。默认站在安全一侧。
- **草稿 API 有门槛。** 需要认证的订阅号/服务号，且要在后台配 IP 白名单。个人未认证号用不了。
- **图片消息正文只收纯文本。** Markdown、HTML 一律剥掉，图文不能交错——它和小红书是两个版式，别指望排版自由。
- **九宫格上的字会糊。** 那是预览图，用来看结构的，不是成品。字看不清很正常，逐张出图时才是真的。
- **生图和对话模型要自己配。** 不配也能用，但文案和配图就得由 Agent 或你自己来。
- **单张图 2MB、单篇 20 张、标题 32 字。** 微信的硬限制，撞上了 CLI 会提前拦，不会让你白传。
- **素材上传有每日配额。** 别拿测试图反复传，会烧额度。

## 常见问题

**Q：为什么不做成 MCP？**
MCP 每个客户端都要单独写配置文件、重启、管进程。CLI 只要对方能跑 shell 就行，而且同一份配置、同一份代码，到处复用。MCP 只是给纯 GUI 客户端的可选外壳，这里不值得。

**Q：能不能发公众号长图文，要排版那种？**
不能，也不打算做。长图文是 `news` 结构，要处理 HTML 排版、封面、摘要，是另一套完全不同的东西。

**Q：重复执行会不会建出一堆草稿？**
`draft/add` 没有幂等，所以 CLI 会记住上次的 `media_id` 和内容摘要，内容没变时会拦下来问你。要覆盖就先 `wxpost draft delete <media_id>`。

**Q：图片会被改坏吗？**
不会。原图在 `post/images/` 保持不动，裁切和压缩的产物在 `post/.build/`，每次重跑先清空再生成。

**Q：多个公众号怎么切？**
`accounts` 下每个 key 一个号，`--account` 指定，不指定就用 `active_account`。

## 关于作者

邱高华，全栈开发工程师，广州。

- Gitee：[@qiu-gaohua](https://gitee.com/qiu-gaohua)
- 邮箱：w928245215@qq.com

## 许可证

MIT — 随便用，随便改，随便造。

```
Copyright (c) 2026 邱高华
```

---

## English

**wxpost** turns a one-line topic into a ready-to-review WeChat Official Account "sticker" post (`article_type=newspic` — the image-message format that looks like Xiaohongshu).

It handles the whole chain: writing the caption, designing a shot list, generating a 3×3 storyboard for review, rendering each image, cropping to 3:4, uploading permanent media, and creating the draft. No MCP — just a CLI, so any agent that can run a shell command can use it.

Each Official Account keeps its own persistent style profile (tone, emoji density, image count, visual style, banned words), so a tool account and a lifestyle account don't end up looking the same.

**Install**

```bash
git clone https://github.com/whishi47/wechat-sticker.git ~/.wxpost && bash ~/.wxpost/install.sh
```

**Use**

```bash
~/.wxpost/wxpost.sh init                  # credentials
~/.wxpost/wxpost.sh write --dir ./post --brief "your topic" --count 9
~/.wxpost/wxpost.sh storyboard --dir ./post
~/.wxpost/wxpost.sh gen --dir ./post
~/.wxpost/wxpost.sh publish --dir ./post  # draft only; add --submit to publish
```

MIT licensed.
