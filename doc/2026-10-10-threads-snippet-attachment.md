# threads 帖子「下方内容」丢失：snippet 长文块

日期：2026-10-10

用户报 `https://www.threads.com/@snowmaple_official/post/DeTKrdrGjrZ`
「抓不到下方内容」。

## 症状

解析只发出「ご参考までに添付いたします。」一句，而页面上还有一封 **623 字的完整长信**
（拝啓 … 敬具），表现为 caption 底下 6 行截断 + "Read more"。

## 根因

帖子正文由**两块**组成，各在不同字段：

| 块 | 字段 | 内容 |
| --- | --- | --- |
| caption | `post.caption.text` | 「ご参考までに添付いたします。」（后跟长文） |
| **附加长文** | `text_post_app_info.snippet_attachment_info.text_fragments.fragments[].plaintext` | 「拝啓 … 敬具」**623 字** |

`ThreadsPost.from_graphql` **只读 `caption.text`**，从未读 `snippet_attachment_info`
→ 长文被静默丢弃。

### 取证路径（三层，别只到第二层就下结论）

1. **页面**：DOM 里长文在 `a[href="/@.../post/<id>/media"]` 卡片内
   （`-webkit-line-clamp: 6` 截断 + `Read more`），属于**该帖**而非引用帖/图片 OCR。
2. **GraphQL（匿名）**：`snippet_attachment_info: null`，
   `DirectQuery` 与 SSR 的 `TargetQuery` 都是 null ⇒ 匿名拿不到。
3. **GraphQL（登录态）**：**同一次 DirectQuery 请求**里
   `snippet_attachment_info.text_fragments.fragments[0].plaintext` = 完整 623 字。
   ⇒ 生产已配 cookie，**零额外请求**即可拿到。

排查时绕过的岔路（记下来省得下次再绕）：
- 先怀疑「图片 OCR/alt 文本」→ 不是（该卡片无 img/video）；
- 又怀疑「引用帖」→ 不是（`share_info.quoted_post` 等全 null）；
- 再试「SSR HTML 提取」→ 能拿到但**多一次请求**（违反"一次请求"原则），
  且登录态 GraphQL 已直接给 ⇒ 弃用那条路。
- ⚠️ **匿名探测会 401 限流**（`Please wait a few minutes… require_login`）——
  连打太多次后连正常请求都挂；生产 cookie 的请求不受影响。

## 修复（`lib/src/parsehub/provider_api/threads.py`）

- `ThreadsPost._snippet_text(post)`：读 `snippet_attachment_info` 的 fragments，
  拼成文本（多片段用空行连接）；缺失/结构不对返回空串。
- `from_graphql`：`content = caption` + `\n\n` + `snippet`（有 snippet 时）。
- `_apply_text_spoilers`：**同时扫 caption 与 snippet 两处的 fragments**
  （长文块里也可能有遮罩行）。
- 拿不到 snippet 时**逐字不变**（老行为）；空 caption 时只出长文。

## 验证

- 新测试 `lib/test/test_threads_snippet.py`（6 条）：长文接上且顺序正确、
  逐字完整、无 snippet 时逐字不变、null/坏结构不炸、空 caption 仍出长文、
  遮罩回归。fixture `threads_snippet_post.json` 从真实（登录态）响应裁剪。
- lib **907 passed**、bot **576 passed**、ruff + pylint 全绿。
- 生产容器端到端：`content_len: 739`，拝啓/敬具均在。

## 教训

- **"正文"可能不止一个字段**：threads 的正文 = caption + snippet 两块。
  与 twitter 的 `note_tweet`（长文）同类问题 —— 接平台时先确认
  "页面上看到的全部文字"分别装在哪几个字段里。
- **同一查询在登录态/匿名态给的数据可以不同**：这个字段匿名是 `null`、
  登录才有。报"拿不到"之前先拿生产同一份 cookie 打一次。
