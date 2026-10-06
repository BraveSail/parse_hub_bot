# 链接卡片的作用域：只在引用/回复里

日期：2026-10-07
触发：用户报「链接的预览放引用里了」→ 修错方向 → 用户澄清
「之前让你修的是 引用里面的链接没有封面，我让你统一，**作用域是引用或者回复**」
「**有引用就引用吗，没引用就不弄，链接你放那里不管就行了**」

## 我要的是什么（一句话）

**引用或回复里**的链接要有封面；作者自己贴在正文里的链接**不动**。

## 走错的两步

1. 最初把「像 bilibili 引用的视频那样」理解成**给它套一个引用块** ——
   于是正文里的链接被渲染成 `> <i>标题</i>`，**无中生有出一个引用**。
2. 发现封面被算进 `quoted_media_count` 后，只改了计数归属（封面归正文），
   但**引用块本身还在** —— 症状没消除，因为问题不是"媒体归哪档"。

教训：用户说"统一"指的是**作用域内**的统一（引用里也要像正文一样有封面），
不是把行为扩大到全文。动手前先问「这个行为该发生在**哪些位置**」，
而不是「这个功能该覆盖**哪些内容**」。

## 改法

`lib/src/parsehub/parsers/parser/twitter.py`

```python
# 只对被引用 / 被回复推文的正文调用 —— 它们本来就渲染成引用卡片
reply_yt  = await _youtube_card(tweet.reply_to.full_text)      if tweet.reply_to      else ("", [])
quoted_yt = await _youtube_card(tweet.quoted_status.full_text) if tweet.quoted_status else ("", [])
```

- 卡片文字插在引用块**内部**（`_append_inside_quote`）：`format_quote_block` 末尾自带
  一个空行作为块结束，插在它之后就成了独立段落，媒体会和卡片分家。
- 封面归**它所属的那一档**：被回复的 → `reply_media_count`，被引用的 → `quoted_media_count`。
- 计数判据在**数据层**（`tweet.quoted_status` / `tweet.reply_to`），
  不是"正文里有没有 `> ` 形态的文字"。

## 验证

主帖正文有 YouTube 链接、无引用无回复（端到端实发）：

```
media=1  quoted_media_count=0
正文里有引用块 = False
正文尾部 = 'https://youtube.com/shorts/hnSeq_P3jAo　…'
```

引用场景（受控渲染，数据取自真实推文）：

```
> <i><a href="https://x.com/ikizulive_staff">イキヅライブ！</a> …</i>
>
> <i>📱Short： https://youtube.com/shorts/hnSeq_P3jAo　</i>
> <i><a href="https://youtube.com/shorts/hnSeq_P3jAo">…</a></i>    ← 卡片在引用块内
```

`quoted_media_count = 2`（引用帖视频 + 它的 YouTube 封面）。

测试：`lib/test/test_youtube_link_scope.py`（作用域的两侧都钉住）。
lib 571 / bot 474 全绿。commit `58ad80f`。
