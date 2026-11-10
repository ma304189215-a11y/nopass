#!/bin/sh
# 网关对外端口同步器（通用）
# 配置查找：$NOPASS_GW_CONF -> /opt/nopass/gateway-sync.conf -> /root/gateway-sync.conf
#   MODE=uci|iptables|pve-vm   VMID=<id>   PUSH=local|pct:<id>|ssh:<host>
#   PUSH_TARGET=/path/gateway-ports.json   EXPORT_BIN=/path/fw-export.py
set -e
for c in "${NOPASS_GW_CONF:-}" /opt/nopass/gateway-sync.conf /root/gateway-sync.conf; do
  if [ -n "$c" ] && [ -f "$c" ]; then CONF="$c"; break; fi
done
if [ -n "$CONF" ]; then . "$CONF"; fi
MODE=${MODE:-uci}
PUSH=${PUSH:-local}
DEST=${PUSH_TARGET:-/opt/nopass/state/gateway-ports.json}
if [ -z "$EXPORT_BIN" ]; then
  for b in /opt/nopass/fw-export.py /root/fw-export.py "$(dirname "$0")/fw-export.py"; do
    if [ -f "$b" ]; then EXPORT_BIN="$b"; break; fi
  done
fi
if [ -z "$EXPORT_BIN" ]; then echo "找不到 fw-export.py"; exit 1; fi
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

case "$MODE" in
  uci)      python3 "$EXPORT_BIN" --source uci --out "$TMP" ;;
  iptables) python3 "$EXPORT_BIN" --source iptables --out "$TMP" ;;
  pve-vm)   python3 "$EXPORT_BIN" --source pve-vm --vmid "${VMID:?需要 VMID}" --out "$TMP" ;;
  *) echo "未知 MODE=$MODE"; exit 1 ;;
esac

SUMMARY=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("%s (source=%s)" % (d.get("public_ports"), d.get("source")))' "$TMP")

case "$PUSH" in
  local) mkdir -p "$(dirname "$DEST")"; cp "$TMP" "$DEST" ;;
  pct:*) CID=${PUSH#pct:}
         pct exec "$CID" -- mkdir -p "$(dirname "$DEST")"
         pct exec "$CID" -- sh -c "cat > $DEST" < "$TMP" ;;
  ssh:*) H=${PUSH#ssh:}
         ssh -o BatchMode=yes "$H" "mkdir -p $(dirname $DEST) && cat > $DEST" < "$TMP" ;;
  *) echo "未知 PUSH=$PUSH"; exit 1 ;;
esac

echo "  public_ports=$SUMMARY"
