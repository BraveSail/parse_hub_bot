# linux.do：本层也标楼层号

日期：2026-10-07
触发：用户「https://linux.do/t/topic/2989140/4?u=libc.so.6 主楼标楼层号了但是回复没标」

## 取证（改前）

真实响应（`lib/test/fixtures/linuxdo_floor_4.json`）：分享的是第 4 楼（`MystDove`，
`reply_to_post_number=null` → 回复主楼）。渲染出来：

```
**<a href="https://linux.do/u/MystDove">@MystDove</a>**        ← 本层(4楼): 没标

> <i><a href="https://linux.do/u/KoaIa">Leo</a> <code>@KoaIa</code> · #1</i>   ← 主楼引用块: 标了
```

缺口是**当前楼层的作者行**没有楼层号：

- 引用块里的其它层由 `LinuxDoTopic._post_to_quote` 自己拼 `head = f"{author} · #{floor}"`；
- 本层走 bot 侧 `plugins/helpers.py::format_author_line`，那条路上**根本拿不到楼层号** ——
  `LinuxDoTopic` 与 `ParseResult` 都没有承载它的字段。

## 设计

楼层号是**内容的位置**，不是 linux.do 专属的渲染模式 ⇒ 给结果基类加一个**通用可选**字段
`position_label`，没有位置概念的平台留空即**零变化**（不引入第二种作者行形态）。
`#` 由平台侧给，通用层不认识楼层语义。

**当前楼层总带楼层号**（主楼 = `#1`）：解析主楼时 `#1` 也是对的，且避免"有时候有有时候没有"。

## 改动

| 文件 | 内容 |
| --- | --- |
| `types/result.py` | 基类加 `position_label` 参数/字段/文档；**四个子类**（Video/Image/Multimedia/RichText）各自重写了 `__init__`，逐个加签名与透传 |
| `provider_api/linuxdo.py` | `LinuxDoTopic` 加 `post_number`，`_from_payload` 填当前楼层 |
| `parsers/parser/linuxdo.py` | `common` 传 `position_label=f"#{topic.post_number}"` |
| `types/serialize.py` | `result_to_cache_dict` / `result_from_cache_dict` / `_BUILDABLE_PARAMS` 带上该字段 |
| `plugins/helpers.py` | `format_author_line` 追加 ` · {position_label}`（在粗体**外面**） |

### 两个必须记住的点

- **四个子类都要改**：基类加参数不够 —— 子类各自重写了 `__init__`，漏一个就
  `TypeError: … got an unexpected keyword argument`（缓存重建时才炸，`test_result_roundtrip` 抓到了）。
- **必须进缓存**：`to_dict()` 是公开输出格式（被测试逐字段冻住）不加字段，所以渲染层要用的
  都得在 `result_to_cache_dict` 里**显式**带上 —— 否则症状是"第一次发有、第二次发没有"。

## 验证

渲染（真实 payload，本地跑真实代码路径）：

```
**<a href="https://linux.do/u/MystDove">@MystDove</a>** · #4      ← 本层，修复前没有

> <i>…Leo… · #1</i>                                                ← 主楼引用块（不动）
```

生产端到端：`position_label = '#4'`、发送成功；读回消息 1004 的块结构，`#4` 与 `#1` 都在。

测试：

- `lib/test/test_result_position_label.py`（10 passed）：字段、四个子类、缓存往返、
  老缓存缺键、provider 填充（4 楼 → 4、不带楼层号 → 1）、parser 传递、空值
- `test/test_author_position_label.py`（7 passed）：作者行末尾、粗体之外、**无标记时逐字不变**、
  整篇渲染、其他平台不含 ` · #`

lib 597 / bot 494 全绿，`scripts/check.sh` 干净。commit `3da476d`。
