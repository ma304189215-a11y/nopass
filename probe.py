#!/usr/bin/env python3
"""外部可达性探测：判断端口是否真的对公网开放。

IPv6 不做 NAT，是判断「是否对外」最干净的通道：
  - 目标地址每次运行时从域名解析（前缀会变，绝不能写死）
  - 经 97 的 HTTP 代理发起，出口是公网 IP，等于外部视角
  - 代理自己的 502/000/504 = 目标不可达 = 不对外
解析结果带缓存（默认 300 秒），解析失败时退回上次已知地址，避免一次抖动就全判为「不对外」。
"""
import concurrent.futures, json, os, socket, subprocess, time

BASE = "/opt/nopass"
CFG = os.path.join(BASE, "config.json")
DEFAULT_HOST = "aalborg.dns.army"
TIMEOUT = 5
DNS_TTL = 300

_cache = {"host": None, "at": 0}


def _cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_host(force=False):
    """解析域名的 IPv6 地址；带 TTL 缓存 + 失败回退。"""
    cfg = _cfg()
    host = cfg.get("probe_hostname") or DEFAULT_HOST
    now = int(time.time())

    if not force and _cache["host"] and now - _cache["at"] < DNS_TTL:
        return _cache["host"]

    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET6)
        addrs = sorted({i[4][0] for i in infos})
        if addrs:
            _cache.update({"host": addrs[0], "at": now})
            try:
                cfg["probe_resolved"] = {"addr": addrs[0], "at": now, "host": host}
                with open(CFG, "w") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return addrs[0]
    except Exception:
        pass

    # 解析失败：用上一次成功的地址
    prev = (cfg.get("probe_resolved") or {}).get("addr")
    if prev:
        _cache.update({"host": prev, "at": now - DNS_TTL + 30})
        return prev
    return cfg.get("public_ipv6") or None


def _proxy():
    """探测用的 HTTP 代理（出口须是公网，才能代表外部视角）。留空=直连。"""
    return _cfg().get("probe_proxy") or ""


def _one(port):
    host = resolve_host()
    if not host:
        return port, False
    url = "http://[%s]:%d/" % (host, port)
    try:
        cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
               "--max-time", str(TIMEOUT)]
        px = _proxy()
        if px:
            cmd += ["-x", px]
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 2)
        code = (p.stdout or "").strip()
        return port, code not in ("502", "000", "", "504")
    except Exception:
        return port, False


def public_reachable(port):
    return _one(port)[1]


def probe_many(ports):
    ports = list(dict.fromkeys(int(p) for p in ports))
    if not ports:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(ports), 16)) as ex:
        return dict(ex.map(_one, ports))


if __name__ == "__main__":
    import sys
    ps = [int(x) for x in sys.argv[1:]] or [5700]
    print("  目标地址: %s" % resolve_host(force=True))
    for p, v in sorted(probe_many(ps).items()):
        print("  :%-6d public=%s" % (p, v))
