#!/usr/bin/env python3
"""qBittorrent 适配器（只读检查）

qBittorrent 自带「按网段白名单免密」：WebUI\\AuthSubnetWhitelist + Enabled=true。
这比注入 token 干净——打开就进，不需要改前端。

但：qbittorrent **运行中会把内存配置周期性写回文件**，外部改文件再重启会被覆盖。
所以本适配器只做**检查与报告**，实际开启请在 WebUI「选项 → Web UI →
勾选"对本地网络免验证"并填网段」里做一次即可，之后永久生效。

用法：
  qbittorrent.py <container>            检查白名单是否已开
  qbittorrent.py <container> revert     无操作（本适配器不改文件）
"""
import re, subprocess, sys

CONF_HINTS = ("/config/qBittorrent/qBittorrent.conf",
              "/config/qBittorrent.conf",
              "/root/.config/qBittorrent/qBittorrent.conf")


def sh(a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def find_conf(c):
    for p in CONF_HINTS:
        r = sh(["docker", "exec", c, "sh", "-c", "test -f %s && echo ok" % p])
        if (r.stdout or "").strip() == "ok":
            return p
    return None


def check(c):
    p = find_conf(c)
    if not p:
        print("no-conf")
        return 1
    r = sh(["docker", "exec", c, "cat", p])
    if r.returncode != 0:
        print("read-fail")
        return 1
    txt = r.stdout
    en = re.search(r'AuthSubnetWhitelistEnabled=(\w+)', txt)
    cidr = re.search(r'AuthSubnetWhitelist=([^\n]*)', txt)
    enabled = bool(en and en.group(1).lower() == "true")
    scope = (cidr.group(1).strip() if cidr else "")
    if enabled and scope:
        print("already (%s)" % scope)
        return 0
    print("not-enabled: 需在 WebUI → 选项 → Web UI 勾选「对本地网络免验证」并填网段")
    return 0


def revert(c):
    print("noop")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "qbittorrent"
    act = sys.argv[2] if len(sys.argv) > 2 else "inject"
    sys.exit(revert(c) if act == "revert" else check(c))
