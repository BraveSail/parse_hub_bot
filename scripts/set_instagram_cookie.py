#!/usr/bin/env python3
"""把新的 Instagram cookie 写进 platform_config.yaml。

- 更新 ``threads.cookies`` (替换为新的, 旧的对同一账号已失效)
- 新增 / 更新 ``instagram.cookies`` (同一个平台登录, 同一条 cookie 串)

用法 (在 161 上, 仓库根目录):
    python3 scripts/set_instagram_cookie.py <cookie文件路径>

cookie 文件内容就是浏览器里复制的那一串 ``k=v; k=v; ...``, 单行即可。
脚本会备份原配置、校验 yaml, 并在最后把 cookie 文件 shred 掉。
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

CONFIG = Path("data/config/platform_config.yaml")
#: 这些平台的 cookie 都来自同一个 Instagram 登录态
SHARED_WITH = ("threads", "instagram")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"cookie 文件不存在: {src}")
        return 2

    cookie = src.read_text(encoding="utf-8").strip().replace("\n", " ")
    if not cookie or "=" not in cookie:
        print("cookie 内容看起来不对 (应为 k=v; k=v; ...)")
        return 2

    names = {seg.split("=", 1)[0].strip() for seg in cookie.split(";") if "=" in seg}
    # 关键字段在不在 (不打印值)
    for must in ("sessionid", "csrftoken", "ds_user_id"):
        if must not in names:
            print(f"⚠️ cookie 里没有 {must} —— 可能复制不全, 该字段是登录态的关键")
    print(f"cookie 字段 ({len(names)} 个): {sorted(names)}")

    backup = CONFIG.with_name(f"{CONFIG.name}.bak-ins-{datetime.now():%Y%m%d%H%M}")
    shutil.copy2(CONFIG, backup)
    print(f"已备份: {backup}")

    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    platforms = data.setdefault("platforms", {})
    for name in SHARED_WITH:
        entry = platforms.setdefault(name, {}) or {}
        old = entry.get("cookies") or []
        entry["cookies"] = [cookie]
        platforms[name] = entry
        print(f"{name}: cookie {len(old)} 条 -> 1 条")

    CONFIG.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # 校验回读
    check = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for name in SHARED_WITH:
        got = ((check.get("platforms") or {}).get(name) or {}).get("cookies") or []
        assert len(got) == 1 and got[0] == cookie, f"{name} 写入校验失败"
    print("✓ 写入并回读校验通过")

    src.unlink(missing_ok=True)
    print(f"已删除临时 cookie 文件: {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
