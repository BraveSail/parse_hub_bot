# 把 pylint 纳入固定流程（以及它抓到的 5 个真问题）

日期：2026-10-04 · 触发：一次生产 ImportError

## 起因：一次"测试全绿但生产挂"的事故

把 `rich_cache_entry` 从 `sender.py` 挪到 `inline_rich.py` 时，顺手写了：

```python
from parsehub.utils.helpers import get_parse_author_name   # ← 真名在 plugins.helpers
```

这是**函数体内的延迟 import** —— 模块加载时不求值，**只有真正写缓存时才炸**。
而 `rich_cache_entry` 那条路径**一行测试都没有**，于是：

- `pytest` 全绿（没覆盖）
- `ruff` 全绿（它不做名字存在性检查）
- 生产：每次上传完成后写缓存都 `ImportError`（用户报的）

**不是"测试没覆盖"这一件事的问题** —— 是收尾环节缺了一道能抓"名字导错模块"的检查。

## 规则：提交前跑 `bash scripts/check.sh`

```bash
==> ruff check
==> pylint --errors-only plugins/ services/ core/ repo/ utils/ db/ lib/src/parsehub
```

已写进 skill（`workflow-and-verification.md`）与固定流程（修 bug 顺序第 ④ 步）。

**验证这个环节真的管用**：把那次写错的 import 临时还原，pylint 立刻报：

```
E0611: No name 'get_parse_author_name' in module 'parsehub.utils.helpers'
```

## pylint 抓到的问题（全部是真问题）

| 码 | 位置 | 问题 |
| --- | --- | --- |
| E0611 | `inline_rich.py` | **本次引入**：延迟 import 导错模块 → 生产 ImportError |
| E1123 | `parsers/parser/linuxdo.py` | 既存：`common` 字典带 `media`，但 `ImageParseResult` 参数是 `photo` → **纯图话题必抛 TypeError** |
| E0701 | `provider_api/kuaishou.py` | 既存：`HTTPStatusError` 在 `RequestError` 之后（前者是后者子类）→ 那个 except 永远执行不到 |
| E0704 | `services/parser.py` ×2 | 既存：重试循环末尾的裸 `raise` 不在 except 里。`max_retries=3` 时不可达，但改成 0 或被重构就抛 `RuntimeError: No active exception`，**盖掉真实失败原因** |
| — | `provider_api/tieba.py` | 既存：`em if (em := ...) else ...` 海象夹在条件表达式里，改成清晰的 `if msg := ...` |

误报（**行内豁免 + 说明**，或 `.pylintrc` 精确豁免）：SQLAlchemy `func.now()`、pydantic `model_fields`、
`asyncio.subprocess.Process`、`haishoku.palette` —— 都是**运行期动态挂上的属性**，pylint 静态解析不到。

**`.pylintrc` 只关这几类框架误报**，抓真问题的码（E0611/E1123/E0701/E0704/E0401…）一律保留 ——
用全局 disable 会把上面这些一起关掉，那就白装了。

## 补上的测试

- `rich_cache_entry`：**之前 0 测试** → 现 3 条（字段搬运完整 / 作者名走 `get_parse_author_name` / 可选字段缺失不崩）
- linux.do parser 的两个分支 → 3 条（纯图走 `photo=`、图文走 `media=` 且带引用块计数、无图不崩）

## 验证

- `bash scripts/check.sh` 全绿；bot 288 passed、lib 435 passed。
- 已部署；生产容器内直接调用 `rich_cache_entry` 成功（`author_name='作者'`，之前必 ImportError）。
- 生产日志 `ImportError` 计数 = 0。

## 教训

1. **延迟 import 是 ruff 与 pytest 的双盲区** —— 必须在收尾环节跑名字存在性检查（pylint）。
2. **"函数没有测试"本身就是风险信号** —— 这次挪动的恰好是唯一没测试的函数。
3. pylint 的价值不在"新代码"，它在**既存代码里翻出了 4 个真 bug**（其中一个纯图话题线上必崩）。
