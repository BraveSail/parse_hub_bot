# pixiv 图床 403：缓存往返丢平台子类（我引入的回归）

日期：2026-10-05
触发：用户报「https://www.pixiv.net/artworks/150374301 403了」→「我试了下还是403啊」

## 根因（已实测确认）

**Redis 缓存迁移（同日早些时候）引入的回归**：`result_to_cache_dict` / `result_from_cache_dict`
只按 `PostType` 往返，映射到四个**通用类**之一 —— 平台子类被降级。

实测对比（同一容器）：

| | 类型 | `_do_download` 来自 |
| --- | --- | --- |
| 现场解析 | `PixivParseResult` | **`PixivParseResult._do_download`**（注入 `Referer`） |
| 缓存往返后 | `MultimediaParseResult` | `ParseResult._do_download`（**没有 Referer**） |

`i.pximg.net` 按 `Referer` 判（实测：带 → 200，不带 → 403）。所以**缓存命中时**下载请求
没有 Referer → 403；而**现场解析**（我手动测）自带 Referer → 200。

这个不对称 —— 复现者失败、我成功 —— 就是它被我误判成"网络/限流"好几轮的原因。

## 排查中排除的（都有实测证据，记下来免得重走）

- 解析失败、出口 IP 被封（152/161 容器/161 宿主同刻全 200）、Referer 没带（`self.headers` 实测非空）、
  Range 分片被拒（1/2/4/8 并发全 206）、并发/频率（顺序 8 + 并发 8 全 200）、TLS 指纹（各种都 200）
- **我一度从异常对象读 `e.response.request.headers` 得到"只有 accept-encoding"，据此怀疑 Referer 丢了 ——
  那是 curl_cffi 错误路径上的**残缺视图**。真实请求头要看 `_headers()` 的返回值或成功响应的 `request.headers`。**

## 修法

- `result_to_cache_dict` 增记 `impl`（具体类名）；`result_from_cache_dict` 优先按它重建
  （构造参数名按 **MRO** 选 —— 平台子类都没有自定义 `__init__`）。
- 老缓存没有 `impl` → 退回通用类（行为与修复前一致，不更糟）。
- yt-dlp 系（`YtVideoParseResult`）**要求运行期句柄 `dl`**，无法从缓存重建 → 捕获 `TypeError`
  退回通用类并 warning（抛异常会让这些平台的缓存**永远读不出来**）。
- **清掉了全部旧缓存**：旧条目无 `impl`，对 pixiv/bilibili/coolapk/douban/douyin/tiktok
  这些"把下载头挂在子类上"的平台都会降级。缓存可再生，代价只是重新解析一次。

## 验证

- 单测：平台子类往返后类型与 `_do_download` 都保留；**所有已注册平台子类**逐个往返；
  需运行期状态的类走兜底；`impl` 不存在时退回通用类。lib 484 passed、bot 413 passed、`check.sh` 干净。
- 真机：现场解析 `PixivParseResult` → 写缓存 → 读回**仍是 `PixivParseResult`** → **用这个重建对象下载成功**
  （2 个文件 1.18MB / 1.37MB）。
- 缓存清理：`shirobako:*` 3 → 0；外部 key 未被误删（前缀隔离仍成立）。

## 教训

1. **"缓存往返后对象行为必须与现场解析一致"是硬约束** —— 序列化一个既有类层次时，
   先问"有没有子类覆盖了行为"（下载头、渲染、类属性都算）。只按枚举往返 = 静默降级，
   而且只有在缓存命中时才发作（复现者与排查者看到的现象不同）。
2. **调试探针读的字段要选"成功路径"的那份**：`e.response.request.headers` 在失败路径上不完整。
3. 排查"只有生产/只有某人失败"时，**先找"两条代码路径"的差异**（这里就是"缓存命中 vs 现场解析"），
   而不是先怀疑网络。
