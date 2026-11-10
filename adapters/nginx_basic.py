#!/usr/bin/env python3
"""nginx Basic Auth 适配器（prompt-optimizer 这类）

思路：不动密码，改 nginx 让**私网来源**直接放行：
  satisfy any;            # 满足任一条件即放行（IP 白名单 或 Basic Auth）
  allow 192.168.0.0/16;   # 私网段
  allow 10.0.0.0/8;
  allow 172.16.0.0/12;
  deny all;
这样内网打开直接进，外网仍需密码 —— 和 nopass 的判定口径一致。

用法：
  nginx_basic.py <container>           加白名单并 reload
  nginx_basic.py <container> revert    撤销
"""
import os, re, subprocess, sys

MARK = "# NOPASS_LAN"
GUARD = "    " + MARK + "\n    satisfy any;\n    allow 127.0.0.1;\n    allow 10.0.0.0/8;\n    allow 172.16.0.0/12;\n    allow 192.168.0.0/16;\n    deny all;\n"
AUTH_CONF_CANDIDATES = ("/etc/nginx/http.d/auth.conf", "/etc/nginx/conf.d/auth.conf")


def sh(a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def find_conf(c):
    for p in AUTH_CONF_CANDIDATES:
        r = sh(["docker", "exec", c, "sh", "-c", "test -f %s && echo ok" % p])
        if (r.stdout or "").strip() == "ok":
            return p
    return None


def inject(c):
    p = find_conf(c)
    if not p:
        print("no-auth-conf")
        return 1
    r = sh(["docker", "exec", c, "cat", p])
    if r.returncode != 0:
        print("read-fail")
        return 1
    txt = r.stdout
    if MARK in txt:
        print("already")
        return 0
    new = txt.rstrip("\n") + "\n" + GUARD
    sh(["docker", "exec", c, "sh", "-c",
        "test -f %s.nopassbak || cp -p %s %s.nopassbak" % (p, p, p)])
    if sh(["docker", "exec", "-i", c, "sh", "-c", "cat > %s" % p], input=new).returncode != 0:
        print("write-fail")
        return 1
    t = sh(["docker", "exec", c, "sh", "-c", "nginx -t 2>&1 | tail -2"])
    if "successful" not in (t.stdout or "") + (t.stderr or ""):
        # 回滚，别把站点弄挂
        sh(["docker", "exec", c, "sh", "-c", "cp -f %s.nopassbak %s" % (p, p)])
        print("nginx-test-failed(%s)" % ((t.stdout or "") + (t.stderr or ""))[:80].replace("\n", " "))
        return 1
    sh(["docker", "exec", c, "sh", "-c", "nginx -s reload 2>/dev/null || true"])
    print("lan-allow")
    return 0


def revert(c):
    p = find_conf(c)
    if not p:
        print("no-auth-conf")
        return 1
    r = sh(["docker", "exec", c, "sh", "-c",
            "test -f %s.nopassbak && cp -f %s.nopassbak %s && echo reverted || echo no-backup" % (p, p, p)])
    out = (r.stdout or "").strip().splitlines()
    if out and out[-1] == "reverted":
        sh(["docker", "exec", c, "sh", "-c", "nginx -s reload 2>/dev/null || true"])
    print(out[-1] if out else "revert-fail")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    act = sys.argv[2] if len(sys.argv) > 2 else "inject"
    sys.exit(revert(c) if act == "revert" else inject(c))
