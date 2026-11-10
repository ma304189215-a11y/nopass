#!/usr/bin/env python3
"""网关对外端口导出器（跨平台）

用途：把「网关/主路由上真正对外开放的端口」导出成一份 JSON，交给 nopass/scan.py 判断。

三种运行方式：
  1. 就在主路由上跑（OpenWrt）：      fw-export.py --source uci --out ./gateway-ports.json
  2. 在任意 Linux 上跑（看本机 nat）： fw-export.py --source iptables --out ./gateway-ports.json
  3. 在 PVE 宿主上读某台 VM 的磁盘：   fw-export.py --source pve-vm --vmid 102 --out ./gateway-ports.json

导出的 JSON 放到 <nopass>/state/gateway-ports.json，或通过 config.json 的
gateway_ports_file 指到别处。nopass 那侧只认这个文件，不关心你从哪弄来的。
"""
import argparse, json, re, subprocess, sys, time


def parse_uci_firewall(txt, family_src="wan"):
    """解析 OpenWrt /etc/config/firewall 文本，返回对外端口明细"""
    out = []
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
                and opts.get("src") == family_src:
            sp = opts.get("src_dport", "")
            if sp.isdigit():
                out.append({
                    "port": int(sp),
                    "to_ip": opts.get("dest_ip", ""),
                    "to_port": int(opts["dest_port"]) if opts.get("dest_port", "").isdigit() else None,
                    "name": opts.get("name", ""),
                    "proto": opts.get("proto", ""),
                })
    return out


def from_uci(path="/etc/config/firewall"):
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        print("uci 读取失败: %s" % e, file=sys.stderr)
        return []
    return parse_uci_firewall(txt)


def from_iptables():
    out = []
    try:
        txt = subprocess.run(["iptables", "-t", "nat", "-S", "PREROUTING"],
                             capture_output=True, text=True, timeout=10).stdout
        txt6 = subprocess.run(["ip6tables", "-t", "nat", "-S", "PREROUTING"],
                              capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        print("iptables 读取失败: %s" % e, file=sys.stderr)
        return []
    for line in (txt + "\n" + txt6).splitlines():
        if "DNAT" not in line or "--dport" not in line:
            continue
        p = re.search(r'--dport\s+(\d+)', line)
        t = re.search(r'--to-destination\s+([0-9a-fA-F:.]+)', line)
        if p:
            out.append({"port": int(p.group(1)),
                        "to_ip": t.group(1) if t else "",
                        "name": "iptables"})
    return out


def from_pve_vm(vmid, part=2, raw_path=None):
    """只读挂载 PVE VM 磁盘的指定分区，读 /etc/config/firewall

    raw_path 可用 --raw 指定，适配非默认存储（如 local-lvm / 其他 pool）。
    """
    raw = raw_path or "/var/lib/vz/images/%s/vm-%s-disk-0.raw" % (vmid, vmid)
    mnt = "/tmp/fw-export-%s-%d" % (vmid, int(time.time()))
    off = (66048) * 512 if part == 2 else 0
    rc = subprocess.run(["mkdir", "-p", mnt]).returncode
    m = subprocess.run(["mount", "-o", "ro,norecovery,loop,offset=%d" % off, raw, mnt],
                       capture_output=True, text=True)
    if m.returncode != 0:
        print("挂载失败: %s" % (m.stderr or "").strip(), file=sys.stderr)
        return []
    try:
        return from_uci(mnt + "/etc/config/firewall")
    finally:
        subprocess.run(["umount", mnt], capture_output=True)
        subprocess.run(["rmdir", mnt], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["uci", "iptables", "pve-vm"])
    ap.add_argument("--vmid")
    ap.add_argument("--firewall", default="/etc/config/firewall")
    ap.add_argument("--raw", help="VM 磁盘 raw 路径（默认 /var/lib/vz/images/<vmid>/...）")
    ap.add_argument("--out", default="gateway-ports.json")
    a = ap.parse_args()

    if a.source == "uci":
        dnat = from_uci(a.firewall)
    elif a.source == "iptables":
        dnat = from_iptables()
    else:
        if not a.vmid:
            sys.exit("pve-vm 需要 --vmid")
        dnat = from_pve_vm(a.vmid, raw_path=a.raw)

    ports = sorted({d["port"] for d in dnat})
    payload = {
        "source": a.source,
        "read_at": int(time.time()),
        "read_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "public_ports": ports,
        "dnat": dnat,
    }
    json.dump(payload, open(a.out, "w"), ensure_ascii=False, indent=2)
    print(json.dumps({"public_ports": ports, "count": len(dnat)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
