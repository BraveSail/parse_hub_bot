# 「开源地址」指向本仓库

日期：2026-10-05
触发：用户「把链接到上游仓库的链接改一下」

## 改了什么

**用户可见的两处**（都是"本 bot 的源码在哪"）：

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| `/start` 文本结尾 | `z-mio/parse_hub_bot` | `BraveSail/shirobako` |
| 限速提示结尾（`demo_mode`） | `z-mio/parse_hub_bot` | `BraveSail/shirobako` |

**没有改** README / `lib/README.md` 里的上游链接。那些写的是"本项目基于
z-mio/parse_hub_bot 与 z-mio/ParseHub 改造"——它是**正确的来源声明**，改了反而变成
错误声明（把别人的项目说成自己的）。同理 `git subtree pull ... ParseHub.git` 那条
操作说明也必须指向上游。

## 关键约束：URL 在源文本里 ⇒ 改 URL = 改键

`i18n` 的键是 `md5(源文本)[:12]`，而这两条源文本**本身就含 URL** ⇒ URL 一改，键就变。
所以每个语言文件都要「删旧键 + 按字典序插新键」，只换 URL 不换键 = 旧键变孤儿、新文本无译。

- 旧键：`c97827ba89ec`（/start）、`4fbdbeb0f163`（限速）
- 新键：`4833c4c22f97`（/start）、`e3889d130a2f`（限速）
- 新键的确认方式是**让引擎自己说**：跑 `i18n.build` 配一个"一被调用就打印并抛错"的
  translator，它报的缺失项正是要找的（`translate_chunk` 收到的是 **id**，所以引擎报
  `2dd0993215ff` = `md5("4833c4c22f97")[:12]`，据此反推确认新键）。
- 源文本用 **AST 还原**（`Constant` / `JoinedStr` → 拼接 + `{表达式源码}`），并对照 yaml
  里已有的旧键自证还原正确。**正则抽不出跨行隐式拼接的文案。**

## 验证（全部实测）

- 引擎：`i18n.build` → `Content unchanged, skipping build`（键齐、无需翻译）
- 体检：`audit_i18n.py` → 缺失 0 / 掩码残留 0 / 占位符不符 0，`i18n 健康`
- 一致性：16 个语言文件键数都是 89
- 数据层：16 语言直读 yaml，两条新键的值都指向新地址、无一处残留旧地址
- 文本层：`build_start_text()` 各语言（zh-hans / en-us / ja-jp / zh-hant / ru-ru）都输出新地址
- **服务端**：真发一条到 DM（msg_id=849）后读回，块里是
  `RichTextUrl(text='GitHub', url='https://github.com/BraveSail/shirobako')`
- 测试：bot 408 passed；`scripts/check.sh` 干净

## 教训

1. **改含 URL/字面量的文案前先确认"这句话是不是 i18n 键的一部分"** —— 是的话，
   改文案 = 改键 + 改值，且要按字典序挪位置。
2. **验证译文别在探针里手拼源文本调 `t_[lang](...)`**：引擎是从**调用处源码**取文本的，
   探针里那串源码与项目里的不是同一段 ⇒ 键不匹配 ⇒ fallback 原文，看起来像"翻译没生效"
   （本次就误报过一次）。要验译文就**直读 yaml**（数据层），或放进项目内的真实现场。
3. `audit_i18n.py` 已修正两处（正则 → AST 抽取；f-string 的 `:.0f`/`!r` 也要还原）——
   修前它把跨行隐式拼接的片段当独立文案、且漏掉格式说明符，每个语言会多报 1~2 条假缺失。
