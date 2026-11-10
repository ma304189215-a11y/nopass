#!/bin/sh
# nopass 安装脚本
# 用法：
#   sudo ./install.sh                  # 装到 /opt/nopass
#   PREFIX=/usr/local/nopass sudo ./install.sh
set -e
PREFIX=${PREFIX:-/opt/nopass}
SRC=$(cd "$(dirname "$0")" && pwd)

echo "[1/5] 创建目录 $PREFIX"
mkdir -p "$PREFIX"
mkdir -p "$PREFIX/adapters"
mkdir -p "$PREFIX/state"
mkdir -p "$PREFIX/log"

echo "[2/5] 复制文件"
cp "$SRC"/scan.py "$SRC"/sources.py "$SRC"/probe.py "$SRC"/fw-export.py "$SRC"/fw-sync.sh "$SRC"/nopass "$PREFIX"/
cp "$SRC"/adapters/*.py "$PREFIX"/adapters/
[ -f "$SRC"/README.md ] && cp "$SRC"/README.md "$PREFIX"/
chmod +x "$PREFIX"/scan.py "$PREFIX"/sources.py "$PREFIX"/fw-export.py "$PREFIX"/fw-sync.sh "$PREFIX"/nopass "$PREFIX"/adapters/*.py

echo "[3/5] 初始化配置"
if [ ! -f "$PREFIX/config.json" ]; then
  cat > "$PREFIX/config.json" <<'JSON'
{
  "public_sources": ["gateway_file", "uci", "iptables", "static"],
  "gateway_ports_file": "/opt/nopass/state/gateway-ports.json",
  "public_ports": [],
  "auto_probe": false,
  "probe_proxy": "",
  "probe_hostname": "",
  "known": {},
  "image_rules": {}
}
JSON
fi
if [ ! -f "$PREFIX/secrets.json" ]; then
  cat > "$PREFIX/secrets.json" <<'JSON'
{
  "_comment": "各服务登录凭据；password 留空则该服务跳过免密"
}
JSON
  chmod 600 "$PREFIX/secrets.json"
fi

echo "[4/5] 建立命令入口"
ln -sf "$PREFIX/nopass" /usr/local/bin/nopass 2>/dev/null || true

echo "[5/5] 安装 cron（每分钟扫描）"
if command -v crontab >/dev/null 2>&1; then
  ( crontab -l 2>/dev/null | grep -v "$PREFIX/scan.py"
    echo "* * * * * /usr/bin/python3 $PREFIX/scan.py >/dev/null 2>&1" ) | crontab -
  echo "  已安装 cron"
else
  echo "  未找到 crontab，请手动添加：* * * * * python3 $PREFIX/scan.py"
fi

echo
echo "完成。下一步："
echo "  1) 配置对外端口来源：nano $PREFIX/gateway-sync.conf"
echo "     或直接：nopass ports 查看当前识别结果"
echo "  2) 填凭据（可选）：nano $PREFIX/secrets.json"
echo "  3) 立即扫描：nopass scan && nopass status"
