#!/usr/bin/env python3
"""本地免密总控（框架版）

作用：扫描本机所有容器的 Web 端口，凡是「不在对外清单里」的，调用对应适配器做免密。
新增镜像时：
  - 同类镜像（image_rules 命中）→ 自动套用已有适配器，无需任何操作
  - 全新类型 → 只登记到 state/log，不动它，等加适配器
"""
import json, os, socket, subprocess, sys, time
sys.path.insert(0, '/opt/nopass')
from probe import public_reachable, probe_many

BASE = "/opt/nopass"
CFG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state", "scan.json")
LOG = os.path.join(BASE, "log", "scan.log")
ADAPTERS = os.path.join(BASE, "adapters")


def log(msg, quiet=False):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not quiet:
        print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def docker_ps():
    out = subprocess.run(["docker", "ps", "--format",
                          "{{.Names}}\t{{.Image}}\t{{.Ports}}"],
                         capture_output=True, text=True).stdout
    res = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, image, ports = parts[0], parts[1], parts[2]
        hosts = []
        for seg in ports.split(","):
            seg = seg.strip()
            if "->" not in seg:
                continue
            hp = seg.split("->")[0].rsplit(":", 1)[-1].strip()
            if hp.isdigit() and int(hp) not in hosts:
                hosts.append(int(hp))
        res.append({"name": name, "image": image, "ports": hosts})
    return res


def is_http(port, timeout=1.5):
    """粗略判断这个端口是不是 Web 服务（返回 HTML 或需要鉴权的 HTTP）。"""
    for path in ("/",):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
            s.settimeout(timeout)
            s.sendall(("GET %s HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                       "User-Agent: nopass-scan\r\n\r\n" % path).encode())
            data = s.recv(4096)
            s.close()
            head = data.split(b"\r\n\r\n", 1)[0].lower()
            if b"http/" in head:
                ct = b""
                for ln in head.split(b"\r\n"):
                    if ln.startswith(b"content-type"):
                        ct = ln
                return b"text/html" in ct or b"application/json" in ct or not ct
        except Exception:
            return False
    return False


def resolve_adapter(cfg, name, image):
    """优先级：容器名精确 > 镜像名规则 > 无"""
    known = cfg.get("known", {})
    if name in known and known[name].get("adapter"):
        return known[name]["adapter"]
    for pat, ad in cfg.get("image_rules", {}).items():
        if pat and pat in image:
            return ad
    return None


def run_adapter(adapter, container, action="inject"):
    path = os.path.join(ADAPTERS, adapter + ".py")
    if not os.path.exists(path):
        return "adapter-missing:%s" % adapter
    try:
        cmd = [sys.executable, path, container]
        if action != "inject":
            cmd.append(action)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        tail = (p.stdout or "").strip().splitlines()
        return ("ok:" + (tail[-1] if tail else "done")) if p.returncode == 0 \
            else "fail:" + ((p.stderr or p.stdout or "")[:180].replace("\n", " "))
    except Exception as e:
        return "exception:%s" % e


def main():
    cfg = load_json(CFG, {})

    # 【权威判据】对外端口由 sources 汇总（网关文件 / 本机 uci / iptables / 手工）
    from sources import collect as collect_public
    pub = collect_public()
    public = set(int(x) for x in pub.get("ports", []))
    gw_ports = public

    # 代理探测只在网关清单缺失时兜底（不再作为主判据）
    auto = bool(cfg.get("auto_probe", False))
    probed = cfg.setdefault("probe_cache", {})
    import time as _t
    now_ts = int(_t.time())
    state = load_json(STATE, {})
    # 去掉非 Web 端口的历史登记
    seen = {}
    containers = docker_ps()
    if auto:
        from probe import resolve_host
        # 每分钟强制重解析一次：IPv6 前缀会变，不能吃缓存
        cur_addr = resolve_host(force=True)
        need = []
        for c in containers:
            for hp in c["ports"]:
                if hp in public:
                    continue
                e = probed.get(str(hp))
                if (not e) or now_ts - int(e.get("at", 0)) > 300 \
                        or e.get("addr") != cur_addr:
                    need.append(hp)
        if need:
            for hp, is_pub in probe_many(need).items():
                probed[str(hp)] = {"at": now_ts, "public": bool(is_pub),
                                   "addr": cur_addr}
                if is_pub:
                    public.add(hp)

    for c in containers:
        name, image = c["name"], c["image"]
        for hp in c["ports"]:
            if hp in seen.get(name, []):
                continue
            seen.setdefault(name, []).append(hp)

            if hp in public:
                prev = state.get(name, {})
                ad = resolve_adapter(cfg, name, image)
                # 端口转对外：只要有适配器就执行撤销（revert 幂等，没注入时返回 no-backup）
                if ad:
                    rev = run_adapter(ad, name, "revert")
                    if not str(rev).endswith("no-backup") and prev.get("status") != "skipped-public":
                        log("[撤销] %-22s :%-5d 已对外，撤离免密 -> %s" % (name, hp, rev))
                    elif prev.get("status") != "skipped-public":
                        log("[跳过] %-22s :%-5d 在对外清单内，不做免密" % (name, hp))
                elif prev.get("status") != "skipped-public":
                    log("[跳过] %-22s :%-5d 在对外清单内，不做免密" % (name, hp))
                state[name] = {"port": hp, "status": "skipped-public",
                               "image": image, "at": int(time.time())}
                break

            if not is_http(hp):
                continue

            ad = resolve_adapter(cfg, name, image)
            if not ad:
                prev = state.get(name, {})
                if prev.get("status") != "no-adapter" or prev.get("port") != hp:
                    log("[登记] %-22s :%-5d 未适配（内网，可免密，缺适配器）" % (name, hp))
                state[name] = {"port": hp, "status": "no-adapter",
                               "image": image, "at": int(time.time())}
                break

            res = run_adapter(ad, name)
            prev = state.get(name, {})
            if prev.get("result") != res or prev.get("port") != hp:
                log("[适配] %-22s :%-5d adapter=%s -> %s" % (name, hp, ad, res))
            state[name] = {"port": hp, "status": "adapted", "adapter": ad,
                           "result": res, "image": image, "at": int(time.time())}
            break

    live = set(c["name"] for c in docker_ps())
    for k in list(state.keys()):
        if k not in live:
            state.pop(k)

    try:
        fresh = load_json(CFG, {})
        fresh["auto_probe"] = auto
        fresh["probe_cache"] = probed
        fresh["public_ports_seen"] = sorted(gw_ports) if gw_ports else []
        fresh["public_sources_used"] = pub.get("sources", [])
        with open(CFG, "w") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
