#!/usr/bin/env python3
"""对外端口来源（可插拔）

每个来源只做一件事：回答「本机有哪些端口，能从公网连进来」。
支持的来源：
  gateway_file  —— 别处（PVE 宿主等）读网关配置后生成的 JSON（现用）
  uci           —— 本机是 OpenWrt，直接 uci show firewall / 读 /etc/config/firewall
  iptables      —— 任意 Linux，解析 nat 表 PREROUTING 的 DNAT
  static        —— config.json 里手工写死的 public_ports

关键过滤：DNAT 的 dest_ip 必须命中本机地址，才算「本机对外」。
否则「网关 8788 → 别的内网机」会被误判成本机 8788 对外。
"""
import json, os, re, socket, subprocess

CFG = "/opt/nopass/config.json"


def _cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def local_addrs():
    """本机全部地址（含 127.0.0.1、0.0.0.0、通配）"""
    addrs = {"127.0.0.1", "0.0.0.0", "::1", "::"}
    try:
        for fam in (socket.AF_INET, socket.AF_INET6):
            for i in socket.getaddrinfo(socket.gethostname(), None, fam):
                addrs.add(i[4][0])
    except Exception:
        pass
    try:
        out = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True).stdout
        for m in re.finditer(r'inet6?\s+([0-9a-fA-F:.]+)/', out):
            addrs.add(m.group(1))
    except Exception:
        pass
    return addrs


def _matches_local(ip, locals_):
    if not ip:
        return True          # 没写 dest_ip = 本机
    ip = ip.strip()
    if ip in locals_:
        return True
    return False


# ---------- 来源 1：网关读出来的文件 ----------
def from_gateway_file(path=None):
    cfg = _cfg()
    path = path or cfg.get("gateway_ports_file") or "/opt/nopass/state/gateway-ports.json"
    try:
        d = json.load(open(path))
    except Exception:
        return None
    locals_ = local_addrs()
    ports, detail = set(), []
    for item in d.get("dnat", []):
        p = item.get("port")
        if not isinstance(p, int):
            continue
        if _matches_local(item.get("to_ip"), locals_):
            ports.add(p)
            detail.append({"port": p, "src": "gateway_file", "to_ip": item.get("to_ip")})
    # 兼容只有 ports 列表的旧格式
    if not detail and d.get("public_ports"):
        for p in d["public_ports"]:
            try:
                ports.add(int(p))
                detail.append({"port": int(p), "src": "gateway_file"})
            except Exception:
                pass
    return {"ports": sorted(ports), "detail": detail} if detail else None


# ---------- 来源 2：本机是 OpenWrt ----------
def from_uci():
    fw = "/etc/config/firewall"
    if not os.path.exists(fw):
        return None
    try:
        txt = open(fw, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None
    locals_ = local_addrs()
    ports, detail = set(), []
    for block in re.split(r'^config ', txt, flags=re.M):
        if not block.strip():
            continue
        head = block.split("\n", 1)[0].strip()
        opts = {}
        for line in block.splitlines()[1:]:
            m = re.match(r'\s*option\s+(\S+)\s+(.*)', line)
            if m:
                opts[m.group(1)] = m.group(2).strip().strip("'\"")
        if head.startswith("redirect") and opts.get("target") == "DNAT" \
                and opts.get("src") == "wan":
            sp = opts.get("src_dport", "")
            if not sp.isdigit():
                continue
            if _matches_local(opts.get("dest_ip"), locals_):
                ports.add(int(sp))
                detail.append({"port": int(sp), "src": "uci", "to_ip": opts.get("dest_ip")})
    return {"ports": sorted(ports), "detail": detail} if detail else None


# ---------- 来源 3：任意 Linux 的 iptables ----------
def from_iptables():
    try:
        out = subprocess.run(["iptables", "-t", "nat", "-S", "PREROUTING"],
                             capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return None
    if not out.strip():
        return None
    locals_ = local_addrs()
    ports, detail = set(), []
    for line in out.splitlines():
        if "DNAT" not in line or "--dport" not in line:
            continue
        p = re.search(r'--dport\s+(\d+)', line)
        t = re.search(r'--to-destination\s+([0-9a-fA-F:.]+)', line)
        if not p:
            continue
        ip = t.group(1) if t else ""
        if _matches_local(ip, locals_):
            ports.add(int(p.group(1)))
            detail.append({"port": int(p.group(1)), "src": "iptables", "to_ip": ip})
    return {"ports": sorted(ports), "detail": detail} if detail else None


# ---------- 来源 4：手工 ----------
def from_static():
    cfg = _cfg()
    pts = cfg.get("public_ports") or []
    ports = []
    for p in pts:
        try:
            ports.append(int(p))
        except Exception:
            pass
    if not ports:
        return None
    return {"ports": sorted(set(ports)),
            "detail": [{"port": p, "src": "static"} for p in sorted(set(ports))]}


SOURCES = {
    "gateway_file": from_gateway_file,
    "uci": from_uci,
    "iptables": from_iptables,
    "static": from_static,
}


def collect():
    """按配置顺序跑所有来源，合并结果。"""
    cfg = _cfg()
    order = cfg.get("public_sources") or ["gateway_file", "uci", "iptables", "static"]
    ports, detail, used = set(), [], []
    for name in order:
        fn = SOURCES.get(name)
        if not fn:
            continue
        try:
            r = fn()
        except Exception:
            r = None
        if r:
            ports |= set(r["ports"])
            detail.extend(r["detail"])
            used.append(name)
    return {"ports": sorted(ports), "detail": detail, "sources": used}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
