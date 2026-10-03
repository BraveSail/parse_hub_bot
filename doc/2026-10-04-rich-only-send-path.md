# parse_hub_bot：收敛为「只有富文本模式」

用户指令（2026-10-04）：「标签换行你觉得，普通路径删掉」、「不要每个平台一套啊，统一接口统一处理」。

## 目标

1. 删掉「media group + caption」老发送路径，所有解析结果一律走富文本（rich message）。
2. 删掉 `rich_mode` 开关（不再有第二种模式，开关没有意义）。
3. 删掉长文的 Telegraph 路径（富文本已能直接还原 markdown 正文，Telegraph 是冗余的第二实现）。
4. **保留** `/raw`、`/zip` 两个显式命令模式（用户主动敲命令才走，不属于默认发送路径）。
5. 标签行过长时截断（由我定的展示策略：按显示宽度预算，超出加省略号）。

## 非目标

- 不动库（ParseHub）的解析逻辑。
- 不动 inline 的「占位 → 选中 → 编辑」流程。

## 阶段

### phase5 — 本地改动清单
- 产出：`LOCAL_FORK_MODIFICATIONS.md`（相对上游 z-mio/parse_hub_bot 的本地改动，按主题归类；参照 mizugram 的同名文件定位）。
- 验证：清单里的每条能在 `git log upstream/main..main`（54 个提交）里找到对应。

### phase6 — README 改造
- 产出：`README.md` 顶部说明本仓库是上游的衍生版本（已脱离 fork 关系）+ 指向本地改动清单；保留原有的功能表/部署/配置说明（对使用者有价值，不删）；补 Credits 与来源链接；修正与现状不符的描述（发送形态已统一为富文本、镜像不再是上游 ghcr）。
- 验证：README 里不再出现"本仓库=z-mio 官方"的暗示；功能描述与实际行为一致。

### phase7 — 脱离 fork 关系并改名 shirobako
- 更正：GitHub **支持就地脱离**（仓库 Settings → Danger Zone → **Leave fork network**），条件为「公开 + <1GB + 无子 fork」，本仓库三条全满足（public / 2.8MB / 0 forks），因此不需要新建仓库再推送那套做法。
- 产出：浏览器进 `BraveSail/parse_hub_bot` 设置页执行 Leave fork network → 改名为 `shirobako` → 本地 `origin` 指向新地址（`upstream` 仍指 z-mio）。
- 注意：离开 fork network **不可逆**，且不保留 issue/PR/wiki/star 等元数据（git 提交历史保留）。执行前先确认本地已推完所有工作。
- 验证：`gh repo view BraveSail/shirobako --json isFork,parent` 为 `false` / `null`。

### phase8 — ~~旧 fork 处置~~（作废）
- 就地脱离后不存在"旧仓库"，本条作废。

## 风险与回退

- 删除面较大（三个分支 + 开关 + Telegraph），测试里有一批针对老 caption 的用例会失败 —— 那些用例随老路径一起删，不要为了保测试而留死代码。
- 回退：`git revert`（改动分散在若干 commit，逐个 revert 即可）；镜像回退 `docker tag shirobakobot:before-merge shirobakobot:local`。
- 仓库迁移期间**不要删旧仓库**，它是最后的退路。
- 确认 `rich_mode` 使用点：`repo/settings/schema.py`、`services/settings.py`、`plugins/settings/models.py`、`plugins/handlers.py` ×3、`plugins/parse/inline.py`。
- 确认老路径代码块：`handlers.py` 的 RichText 分支（Telegraph）、无媒体分支（`text_no_preview` + caption + 写缓存）、有媒体分支（`send_media` + caption）。
- 验证方式：`grep -rn` 列出全部引用点，改完后再 grep 一次确认归零。

### phase1 — 标签行截断
- 产出：`plugins/helpers.py::format_tags` 按显示宽度预算截断，超出补 `…`；`test/test_text_layout.py` 覆盖（长标签集被截断、短标签集不截断、截断后仍都是合法链接）。
- 验证：`uv run pytest test/test_text_layout.py`；再用真实 pixiv 链接 dump 一眼。

### phase2 — 删 Telegraph 路径
- 产出：`handlers.py` 不再调用 `create_richtext_telegraph`；`CacheEntry.telegraph_url` 字段与相关写入移除（若其它地方仍需要则保留字段、只去调用）。
- 验证：`grep -rn "telegraph" plugins/ services/` 只剩无害引用；pytest 通过。

### phase3 — 删 rich_mode 开关与老发送分支
- 产出：
  - `handlers.py`：三个 `if req.config.rich_mode:` 变为无条件 `send_rich_media`，删除其后的老分支代码（caption 构造、`send_media`、`text_no_preview`、相应写缓存）。
  - `plugins/parse/inline.py`：`if config.rich_mode:` 去掉，直接构建富文本项。
  - `repo/settings/schema.py` / `services/settings.py` / `plugins/settings/models.py`：删 `rich_mode` 字段与设置项。
- 验证：`grep -rn "rich_mode"` 归零；pytest 全绿；ruff 通过。

### phase4 — 部署与真机验证
- 产出：commit + push + 161 pull + build + up；用 session 副本法（见 skill 的限流坑）真实发送一条，dump 服务端块确认排版。
- 验证：容器日志出现 `[Watchdog] Bot 开始运行...`；用户侧实际渲染确认。

## 风险与回退

- 删除面较大（三个分支 + 开关 + Telegraph），测试里有一批针对老 caption 的用例会失败 —— 那些用例随老路径一起删，不要为了保测试而留死代码。
- 回退：`git revert`（改动分散在若干 commit，逐个 revert 即可）；镜像回退 `docker tag shirobakobot:before-merge shirobakobot:local`。
