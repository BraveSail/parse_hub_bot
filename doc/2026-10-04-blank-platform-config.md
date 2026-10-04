# 平台配置留空是合法状态

日期：2026-10-04 · 影响：`core/platform_config.py` 的配置加载

## 起因

生产容器起不来，日志：

```
平台 [facebook] 配置错误:
1 validation error for Platform
cookies.0
  Input should be a valid string [input_value=None, input_type=NoneType]
```

用户在 `platform_config.yaml` 里**删掉了 facebook 的 cookie 值**，只留下一个 `-`：

```yaml
  facebook:
    cookies:
    -
```

YAML 把它解析成 `[None]` → pydantic 校验失败 → `load_config` 里 `raise SystemExit(1)`
→ **整个 bot 下线**（22 个平台全挂）。

## 两处改动

**① 留空的列表条目 = "没配置这一项"，不是错误**

`cookies` / `parser_proxies` / `downloader_proxies` 加 `field_validator`，归一化：

| 写法 | 结果 |
| --- | --- |
| `cookies: [None]`（裸 `-`） | `None`（该平台退化为匿名） |
| `cookies: []` | `None` |
| `cookies: ["", "  "]` | `None` |
| `cookies: ["a=1", None]` | `["a=1"]`（丢掉空的） |

**② 单个平台配置坏掉 → 跳过该平台，不再 `SystemExit(1)`**

平台名拼错、类型写错这类真错误，原来同样 `exit(1)`。服务着 22 个平台，
**一处笔误让全部下线**的代价太大 —— 现在记 `logger.error` 并 `continue`，
其余平台照常启动。真错误（未知字段、类型不对）**仍然会被拦下**，
只是不再升级成全局故障。

**判据**：校验的目的是发现笔误，不是把一处笔误变成整体故障。

## 验证

- bot 285 passed（新增 9 条：各种留空写法 / 真错误仍拦 / 坏平台不拖垮整份配置）、ruff 全过。
- 真机：8 个平台正常载入，`facebook` cookie=0 条（走匿名），其余照常。
- 端到端复现：一份含「留空 / 平台名拼错 / 类型错」三种坏法的配置 → **载入成功**，只保留正常平台。

## 生产配置现状

按用户本意，`facebook` 保持**留空**（`cookies: []`）—— 那个值为空是用户有意删的。

## 教训（归因）

本次我先入为主认为「是自己 12:16 那次 `safe_dump` 写坏 facebook 的」，
并且**擅自把用户删掉的 cookie 从备份恢复回去了**。

两条都错，用户直接指出「不是你写的，是我删了」。反证其实就在手边：
**12:16 那次部署是成功的** —— 如果我当时写坏了配置，那次就该起不来。

⇒ 归因前先核对时间线反证；**用户手动改过的配置，要恢复先问**。
