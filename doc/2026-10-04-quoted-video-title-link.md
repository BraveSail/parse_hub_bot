# 引用视频的标题链到视频

日期：2026-10-04 · 影响：bilibili 转发动态（引用块）

## 需求

用户：**「这个引用的视频标题弄成超链接，链接到视频」**。

引用块里只有封面图，**没有视频本身** —— 标题若是纯文本，就是「看得到是什么、进不去」。普通视频动态不同（正文里有视频文件），所以只改引用块。

## 改动

1. **`BiliDynamic` 存 `bvid`** + `video_url` property（拼 `https://www.bilibili.com/video/{bvid}`）。
2. **`_parse_av` 不再丢字段**：原实现

   ```python
   if content := cls._get_desc_text(module_dynamic):
       return cls(content=content)        # ← 只返回正文, archive 的 title/cover/bvid 全丢
   ```

   动态自己带正文时（分享时写的话术），视频标题、封面、BV 号**全部丢失**。现在两条分支合并，`title`/`images`/`bvid` 恒取 archive，`content` 优先用动态正文、其次用视频简介。
3. **`_render_forward` 把标题渲染成链接**：

   ```
   > <i><a href="https://space.bilibili.com/224267770">夏日幻听MCE</a>：</i>
   > <i><a href="https://www.bilibili.com/video/BV1UqHi6uEie">「脑洞学生会！」第1话【中文字幕】</a></i>
   > <i>「脑洞学生会！」第1话</i>            ← 简介保持纯文本
   ```

   没有 `bvid`（非视频转发）时标题保持纯文本。

## 验证

真机 dump 服务端块：

```
块2: RichBlockBlockQuotation
  RichTextItalic     作者名 + 主页链接
  RichTextItalic     视频标题 → https://www.bilibili.com/video/BV1UqHi6uEie   ← 可点
  RichTextItalic     「脑洞学生会！」第1话 (简介, 无链接)
```

lib 412 passed（新增 3 条：标题带链接 / 无 bvid 保持纯文本 / desc 存在时 title+cover+bvid 不丢）、bot 271 passed、ruff 全过。

## ⚠️ 缓存同步（第二次踩）

持久缓存 **TTL 7 天**，存的是 **parser 输出的 `content`** —— 所以 **parser 层的渲染改动会固化在缓存里**，改完不清缓存，用户命中旧记录时看到的还是旧样子。

本次实测：这条 url 已命中缓存，缓存里的 content 标题**没有链接**。已用 `CacheRepo.remove_by_keys` 清掉全部 9 条（不裸写 SQL）。

判断是否要清缓存：改动落在 **parser/provider**（产出 content）还是要清；落在 **`build_rich_markdown`**（消费 content 现场渲染）则不用。
