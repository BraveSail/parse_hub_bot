# shirobako 与 ParseHub 合并为单仓库

用户指令（2026-10-04）：「把 bot 和库合并」→ 选定方案 **1（库并进 bot）**；「不留了」= `BraveSail/ParseHub` 仓库不再保留（合并完成后删除）。

## 现状（phase0 取证已完成）

- bot `/root/parse_hub_bot`（GitHub: `BraveSail/shirobako`，main，改名已完成、fork 关系待脱离）
  - 通过 PyPI 依赖 `parsehub>=2.2.4`（`pyproject.toml`）；`uv.lock` 里 `source = { registry = "https://pypi.org/simple" }`
  - 部署时用 `compose.deploy.yaml` 的 `additional_contexts: parsehub: ../ParseHub` + `Dockerfile.deploy` 里
    `uv pip install --no-deps --reinstall /opt/parsehub` 覆盖安装本地 checkout
  - 9 个文件、37 处 `from parsehub ...` / `import parsehub`
- 库 `/root/ParseHub`（GitHub: `BraveSail/ParseHub`，master，**已非 fork**）
  - 包结构 `src/parsehub/`，自带 `test/`、`pyproject.toml`（name = parsehub）
  - 相对上游 z-mio/ParseHub：24 个提交、51 个文件、+2974/−73
- 两仓库都有 `upstream` remote 指向 z-mio 的对应仓库（合并后保留，用于继续拉上游）。

## 目标

1. 库源码以 **git subtree** 并入 bot 仓库的 `lib/`，保留库的完整提交历史。
2. bot 改为**本地路径依赖**（`[tool.uv.sources] parsehub = { path = "lib", editable = true }`），
   去掉 PyPI 覆盖安装那一套（`additional_contexts` + `uv pip install /opt/parsehub`）。
3. 单仓库即可构建、测试、部署。
4. 合并后删除 `BraveSail/ParseHub` 仓库（用户明确「不留了」），并把库的本地改动清单并入
   bot 仓库的 `LOCAL_FORK_MODIFICATIONS.md`。
5. **不改任何 `from parsehub import ...` 调用点**（包名与导入路径保持不变）。

## 非目标

- 不重写库的代码结构，不把 `src/parsehub` 提升到仓库根（保留其独立包与自身 pyproject/test）。
- 不动 bot 的业务逻辑。

## 阶段

### phase1 — subtree 并入
- 先提交 ParseHub 现有未提交改动（README 重写 + 新增 LOCAL_FORK_MODIFICATIONS.md），保持历史干净。
- `git subtree add --prefix=lib <ParseHub repo> master`（保留历史）。
- 验证：`lib/src/parsehub/` 存在；`git log lib/ | head` 能看到库的历史；bot 代码零改动。

### phase2 — 依赖与构建改造成单仓库
- `pyproject.toml`：加 `[tool.uv.sources] parsehub = { path = "lib", editable = true }`，去掉
  「Dockerfile 覆盖安装」的注释。
- `uv lock` 重生成，确认 lock 里 parsehub 变成 `source = { editable = "lib" }`；
  `uv sync` 后 `python -c "import parsehub; print(parsehub.__file__)"` 应指向仓库内 `lib/`。
- `Dockerfile.deploy`：删掉 `additional_contexts` 用法与 `uv pip install /opt/parsehub` 段，
  改为 `COPY lib ./lib` 后 `uv sync --frozen`；`compose.deploy.yaml` 删 `additional_contexts`。
- 顺带处理 `Dockerfile`（非 deploy 版）同样问题。
- 验证：本地 `uv run pytest test/ -q` 全绿；`uv run python -c "import parsehub"` 指向 `lib/`。

### phase3 — 构建与容器验证（真实产物）
- 本地 `docker compose -f compose.deploy.yaml build bot`（不需要同级 ParseHub 目录）。
- `up -d` 后容器内：`python -c "import parsehub; print(parsehub.__file__)"` 必须是 `/app/lib/src/parsehub/__init__.py`（或等价安装路径），
  并真实解析一条链接（threads 那条）确认库的本地改动生效。
- 验证：容器日志 `[Watchdog] Bot 开始运行...`；解析输出含本地改动特征（tags / published_at / author_url）。

### phase4 — 推送与清理
- 推送 shirobako；更新 README（单仓库说明、构建步骤去掉「克隆两个仓库」）与
  `LOCAL_FORK_MODIFICATIONS.md`（并入库侧改动章节）。
- 删除 `BraveSail/ParseHub`（用户已确认）—— **删除前再次确认合并结果可用**。

### phase5 — 文档归档
- 计划归档到 `see.moe-repo/doc/`，并把「两仓库合并」的做法与坑写进
  `social-media-parser-development` skill。

## 风险与回退

- subtree 会引入库的完整历史，仓库体积增大（库 .git 约几 MB）——可接受。
- 删除 ParseHub 仓库**不可逆**：删前确认 shirobako 上合并结果已推送且容器验证通过。
- 回退：合并前记录 bot 仓库 tip（`git rev-parse HEAD`），必要时 `git reset --hard <tip>`；
  ParseHub 仓库在被删前本地 `/root/ParseHub` 仍保留一份完整 checkout。
- 上游同步方式变化：库的上游更新改为 `git subtree pull --prefix=lib <ParseHub upstream> master`。
