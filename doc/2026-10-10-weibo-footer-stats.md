# 微博页脚统计：时间 / 点赞 / 评论 / 播放量

日期：2026-10-10

用户问「微博抓不到页脚数据？」。

## 结论：不是抓不到，是 parser 没接

数据全部在**同一次响应**里（详情 API 本来就要发的那次请求），零额外请求：

| 数据 | 字段 | 形态 |
| --- | --- | --- |
| 发布时间 | `created_at` | `"Sat Oct 10 15:17:21 +0800 2026"`（`to_datetime` 已支持该格式）|
| 点赞 | `attitudes_count` | 整数或字符串 |
| 评论 | `comments_count` | 整数或字符串 |
| **视频播放量** | `page_info.media_info.online_users_number` | **精确整数**（名不副实，见下）|

**找不到的**：图微博的浏览量 —— `reads_count` 恒为 null（平台只对博主可见）→ 按
「拿不到就不显示」原则不接。

## `online_users_number` 的语义取证（名字误导）

字段名叫"在线人数"，但实测它就是**累计播放量**：与该视频 TV 接口
（`weibo.com/tv/api/component`）的 `play_count` 对照：

| 视频 | detail API `online_users_number` | TV API `play_count` | 判 |
| --- | --- | --- | --- |
| 丁丁 | 81818 | `"8.1万"` | ✓ |
| 心动的信号 | 408842 | `"40.8万"` | ✓ |
| 任嘉伦 | 8338.5 | `"8,340"` | ✓（是同一数，探测脚本正则没认千分位）|
| 甲 | 310248 | `"31万"` | ✓ |
| 乙（同视频）| 310248 | `"31万"` | ✓ |
| 飞天奖 | 5843688 | （TV 接口无此视频）| — |

6 样本全部吻合 ⇒ 播放量。TV 接口与详情 API 给**同一个数**，只是形态不同
（精确整数 vs 中文缩写）。

## 实现

- **provider**（`provider_api/weibo.py`）：
  - `Data` 加 `created_at` / `attitudes_count` / `comments_count` 字段 + `published_at` /
    `like_count` / `reply_count` 属性（`to_datetime` / `to_int` 归一化）。
  - `MediaInfo` 加 `online_users_number` 字段 + `play_count` 属性。
  - `WeiboTVContent` 加 `published_at`（`real_date`）/ `play_count` / `like_count` / `reply_count`。
  - **新增 `parse_weibo_count()`**：中文缩写解析（`"8.1万"` / `"8,340"` / `"1.2亿"`）——
    youtube 的 `_compact_count` 只认 K/M/B，中文站点要自己的。
- **parser**：三个构造点（TV / page_info 视频 / 图集+混排）全部带上四个字段。
- **渲染层 label**：微博的 reply 位叫**「评论」**（站点 UI 的字样），新增
  `_REPLY_LABEL_KEYS = {Platform.WEIBO: "评论"}` + `metadata_reply_label()`，
  与 bgm 的「状态」（`_LIKE_LABEL_KEYS`）同机制；caption 与富文本两条路径都换。
- **i18n**：新词条「评论」手工插入 16 语言（本机无 OPENAI_API_KEY）；顺手清掉
  **死词条「解 析 中...」**（2026-10-09 首帧改造后代码零引用，见下节）。

## ⚠️ i18n 的形态坑（本次差点误删活词条）

**「状态」「评论」这类"平台 label"词条的调用形态是 `translate(key)`（变量传参）** ——
`easy_ai18n` 的静态扫描只认 `t_()` / `_t()` 的**字面量调用点**，看不到它们，于是：

- 跑全量 `i18n.build` 的 stale 清理会把这**活词条**当孤儿从 16 个 yaml 里删掉
  （2026-10-10 实际发生：「状态」被删过，`git checkout` 恢复）；
- 运行时翻译**是正常工作的**（实测：状态→狀態/Reactions/状態，评论→評論/Comments/コメント）。

⇒ **别跑全量 build 清词条**；维护这类词条走手工（`Text.id_of(源文本)` 求 key →
排序插入/单行删除 → 逐语言 `yaml.safe_load` 校验）。清理死词条时也手工删，
否则会连带删掉同形态的活词条。

「解 析 中...」（`d59c948c471c`）就是这样一条死词条：首帧改造把最后一个调用点删了，
但因为没人敢跑 build，它一直躺在 16 个 yaml 里。本次手工清掉。

## 验证

- lib **901 passed**（新增 `test_weibo_footer.py` 18 条：缩写解析 11 参数组、
  Data/TVContent 字段、三个构造点透传、缺字段降级 None）；bot **576 passed**
  （`test_metadata_reply.py` 新增 5 条：微博→评论、其余→回复、互不串位）。
- `scripts/check.sh`（ruff + pylint --errors-only）全绿。
- **生产容器端到端**（部署后）：

| 链接 | 页脚 |
| --- | --- |
| 图集 `Rm1w5iaAD` | `2026年10月10日 15:17 · 41 点赞 · 8 评论 · 来源（微博）` |
| 视频 `RhvyqEuTH` | `2026年9月10日 22:34 · 81,818 查看 · 63 点赞 · 2 评论 · 来源（微博）` |

## 教训

- **字段名会骗人**：`online_users_number` 是播放量。- 取证方法是**跨接口对照**
  （同一内容在两个接口里的同一数值，形态不同但数一致），而不是相信名字。
- **「字典值」词条在 i18n 里是静态不可见的**：改成这种形态的那一刻起，
  全量 build 就成了它的敌人。接新平台 label 时记住这条。
