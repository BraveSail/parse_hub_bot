# 微博媒体下载 403：下载请求缺 Referer

## 症状

用户分享 `https://weibo.com/1886672467/Rm1w5iaAD?pagetype=profilefeed`（6 图），bot 报：

```
下载错误: 下载失败: 下载错误: 达到最大重试次数，下载失败: 下载错误: HTTP错误: 403
```

## 根因（已取证）

### 生产日志里直接可见请求头

```
下载请求被拒: url=https://wx3.sinaimg.cn/large/70745653ly1ihxa54f4bjj20m9091gr6.jpg status=403
    请求头 = {'accept-encoding': 'identity', 'range': 'bytes=0-'}          ← 没有 Referer
    响应头 = {'server': 'Byte-nginx', ..., 'x-ban': 'MISS,77113',
              'byte-error-code': '00068', 'x-exception-info': 'deny code 68',
              'via': 'cache02.oversea-JP-OSA2'}
```

火山引擎 CDN 的 `deny code 68` / `x-ban` 是**拒绝**标记。

### 受控对照（161 容器，curl_cffi chrome150，唯一变量 = Referer）

| 请求 | 结果 |
| --- | --- |
| 无 Referer 无 Range | 403 |
| 无 Referer + `Range: bytes=0-`（=生产形态） | 403 |
| **+ `Referer: https://weibo.com`** | **200 / 206，全量 215577 字节** |
| + `Referer: https://m.weibo.cn/` | 206 |

host 对照：`wx1/wx2/wx3/wx4.sinaimg.cn` 全部 403；同一容器里**其它平台**（pbs.twimg.com 等）正常。

视频 CDN 同病（`f.video.weibocdn.com`，1KB Range 探测）：

| 请求 | 结果 |
| --- | --- |
| 无 Referer | 403（同样 Byte-nginx） |
| + Referer | 206 |

### 代码缺口

`WeiboParser` 产出的结果是**通用类**（`ImageParseResult` / `MultimediaParseResult` /
`VideoParseResult`），没有 `_do_download` 覆盖 → 下载 headers 为 `None` → 不带 Referer。
项目里 pixiv / douban / douyin / bilibili 早有同名模式（平台子类注入 Referer），微博是漏网。

## 修复（`lib/src/parsehub/parsers/parser/weibo.py`）

```python
REFERER = "https://weibo.com"

class WeiboParseResult(ParseResult):
    async def _do_download(self, *, ...):
        headers = {"Referer": REFERER}
        return await super()._do_download(..., headers=headers, ...)

class WeiboVideoParseResult(WeiboParseResult, VideoParseResult): ...
class WeiboImageParseResult(WeiboParseResult, ImageParseResult): ...
class WeiboMultimediaParseResult(WeiboParseResult, MultimediaParseResult): ...
```

三个构造点（TV / page_info 视频 / 图集 / 无媒体）全部换成子类。

**为什么必须是子类而不是改基类**：结果层缓存按类名（`impl`）重建 —— 若缓存命中时
降级回通用类，`_do_download` 覆盖不执行，下载又会 403（pixiv 2026-10-05 学到的
`2026-10-05-cache-subclass-regression.md`）。

## 验证

### 离线

- 新测试 `lib/test/test_weibo_download_referer.py`（11 条）：图 / 视频 / 混排三种下载
  注入、子类 `type` 不被 MRO 改掉、缓存往返保留子类、两个真实 fixture 走通对应构造点。
  fixture 从真实详情 API 响应裁剪（`weibo_image_post.json` / `weibo_video_post.json`）。
- lib 全量 **883 passed**（零回归）；bot 侧 **571 passed**；
  `scripts/check.sh`（ruff + pylint --errors-only）全绿。

### 生产（161 容器，已部署代码）

- 图集：`RESULT TYPE: WeiboImageParseResult`，6 张全下载（1,380,739 B；首张 215577 B
  与对照测试完全一致）。
- 视频：`RESULT TYPE: WeiboVideoParseResult`，`笑死我了.mp4 323,660 B`。
- Redis 扫描：0 条微博缓存残留（无老 impl 挡路）。

## 教训

- **403 先看落盘请求头**。下载器日志把「实际发出的请求头」打出来之后，这类问题一眼可辨
  （对比 pixiv 当年靠猜）。加日志的投入直接回本。
- **对照一次只变一个变量**：UA / Range / Referer 三个都单独验过，唯一见效的是 Referer；
  非浏览器 UA 或带 Referer 都能过 —— 说明是「Referer 缺失」判定，不是指纹判定。
- 同平台不同 CDN（图床 sinaimg / 视频 weibocdn）**都要单独验**：同一份 Referer 恰好都放行，
  但不能假设（当初也没有假设 —— 是两个都实打实测过才写的注入）。
