"""部署就绪判定: 日志已就绪 **且** 真的连着 Telegram。

只看到 "[Watchdog] Bot 开始运行" 是不够的 —— 曾出现日志打完却完全不消费更新的情况
(容器活着、连接却是断的)。所以再加一条硬判据: 容器内存在到 Telegram 的 ESTABLISHED 连接。

用法: python scripts/wait_ready.py [容器名] [超时秒数]
退出码 0 = 就绪, 1 = 超时。
"""

from __future__ import annotations

import subprocess
import sys
import time

READY_MARKER = "Bot 开始运行"
TELEGRAM_NET_PREFIXES = ("149.154.", "91.108.")
ESTABLISHED = "01"  # /proc/net/tcp 里的连接状态

#: 就绪检查的探针: 在容器内跑, 打印 "yes"/"no"
_CONN_PROBE = """
import sys
try:
    lines = open("/proc/net/tcp").readlines()[1:]
except OSError:
    sys.exit(1)
for line in lines:
    parts = line.split()
    if len(parts) < 4:
        continue
    try:
        ip_hex, _port = parts[2].split(":")
    except ValueError:
        continue
    if len(ip_hex) != 8:
        continue
    ip = ".".join(str(int(ip_hex[i:i + 2], 16)) for i in (6, 4, 2, 0))
    if ip.startswith(("149.154.", "91.108.")) and parts[3] == "01":
        print("yes")
        break
"""


def _logs_ready(container: str) -> bool:
    result = subprocess.run(
        ["docker", "logs", "--tail", "80", container],
        capture_output=True, text=True, check=False,
    )
    return READY_MARKER in (result.stdout + result.stderr)


def _connected(container: str) -> bool:
    result = subprocess.run(
        ["docker", "exec", container, "python", "-c", _CONN_PROBE],
        capture_output=True, text=True, check=False,
    )
    return "yes" in result.stdout


def wait_ready(container: str, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _logs_ready(container) and _connected(container):
            return True
        time.sleep(1)
    return False


def main() -> int:
    container = sys.argv[1] if len(sys.argv) > 1 else "shirobakobot-bot-1"
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 90
    started = time.monotonic()
    if wait_ready(container, timeout):
        print(f"就绪: 日志已就绪且已连接 Telegram (用时 {time.monotonic() - started:.0f}s)")
        return 0
    print(f"未就绪: {timeout:.0f}s 内未同时满足日志与连接判据", file=sys.stderr)
    subprocess.run(["docker", "logs", "--tail", "8", container], check=False)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
