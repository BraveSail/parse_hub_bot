#!/usr/bin/env bash
# 在服务器上部署 shirobakobot 并**观测就绪**, 而不是固定 sleep。
#
# 为什么不用固定 sleep: 实测真实构建只 2~20 秒, 写死 sleep 30 既浪费又不可靠
# (重建后偶尔要更久才算真就绪)。就绪判据交给 scripts/wait_ready.py。
#
# 用法: bash scripts/deploy_remote.sh
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-compose.deploy.yaml}
SERVICE=${SERVICE:-bot}
CONTAINER=${CONTAINER:-shirobakobot-bot-1}
WAIT=${WAIT:-90}

echo "==> 拉取代码"
git pull --ff-only

echo "==> 构建镜像"
sudo docker compose -f "$COMPOSE" build "$SERVICE"

# --force-recreate: 没有它时 compose 可能因为"镜像 tag 相同"而直接说 Running,
# 容器继续跑旧代码 —— 那就等于白部署了
echo "==> 重建并启动容器"
sudo docker compose -f "$COMPOSE" up -d --force-recreate "$SERVICE"

echo "==> 等待就绪 (判据: 日志 + 到 Telegram 的 ESTABLISHED 连接)"
python3 scripts/wait_ready.py "$CONTAINER" "$WAIT"

echo "==> 部署完成"
