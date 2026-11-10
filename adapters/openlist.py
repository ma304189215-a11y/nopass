#!/usr/bin/env python3
"""OpenList 适配器

两个难点，各自的解法：
  1. 前端是 Go embed，容器内没有 index.html 可改
     → 用官方注入点「设置 → 全局 → 自定义头部」(`customize_head`)，会写进每个页面 <head>
  2. 容器内没有 python3，且 /opt/openlist/data.db 是宿主机挂载
     → 登录用宿主机 urllib 打 127.0.0.1:5244，写库直接改宿主机上的 data.db

凭据从 /opt/nopass/secrets.json 读：
{"openlist": {"url": "http://127.0.0.1:5244", "username": "admin", "password": "xxx"}}
密码为空 → 报 need-password，不猜、不改用户密码。

用法：
  openlist.py <container>         注入
  openlist.py <container> revert  撤销
"""
import json, os, re, sqlite3, subprocess, sys, urllib.request

SECRETS = "/opt/nopass/secrets.json"
MARK = "NOPASS_OPENLIST"
DB_CANDIDATES = ("/opt/openlist/data.db", "/opt/openlist/data/data.db", "/data/data.db")


def find_db():
    for p in DB_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def get_secret():
    try:
        return json.load(open(SECRETS)).get("openlist") or {}
    except Exception:
        return {}


def login(spec):
    url = (spec.get("url") or "http://127.0.0.1:5244").rstrip("/") + "/api/auth/login"
    body = json.dumps({"username": spec["username"], "password": spec["password"]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return (d.get("data") or {}).get("token")


def read_head(db):
    c = sqlite3.connect(db)
    try:
        r = c.execute("select value from x_setting_items where key='customize_head'").fetchone()
        return r[0] if r else ""
    finally:
        c.close()


def write_head(db, val):
    c = sqlite3.connect(db)
    try:
        c.execute("update x_setting_items set value=? where key='customize_head'", (val,))
        c.commit()
        return True
    finally:
        c.close()


def build(tok):
    return ('<script>/*%s*/(function(){var h=location.hostname;'
            'if(!/^(localhost|127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)/.test(h))return;'
            'localStorage.setItem("token",%s);})();</script>' % (MARK, json.dumps(tok)))


def inject(c):
    db = find_db()
    if not db:
        print("no-db")
        return 1
    spec = get_secret()
    if not spec.get("password"):
        print("need-password")
        return 1
    try:
        tok = login(spec)
    except Exception as e:
        print("login-fail:%s" % str(e)[:80])
        return 1
    if not tok:
        print("empty-token")
        return 1

    cur = read_head(db)
    if MARK in cur:
        print("already")
        return 0
    bak = db + ".head.bak"
    if not os.path.exists(bak):
        open(bak, "w").write(cur)
    if not write_head(db, cur + build(tok)):
        print("write-fail")
        return 1
    # openlist 把设置缓存在内存里，改库后必须重启才生效
    subprocess.run(["docker", "restart", c], capture_output=True, timeout=120)
    print("injected(restarted)")
    return 0


def revert(c):
    db = find_db()
    if not db:
        print("no-db")
        return 1
    cur = read_head(db)
    if MARK not in cur:
        print("no-backup")
        return 0
    newv = re.sub(r'<script>/\*%s\*/.*?</script>' % MARK, "", cur, flags=re.S)
    if not write_head(db, newv):
        print("write-fail")
        return 1
    subprocess.run(["docker", "restart", c], capture_output=True, timeout=120)
    print("reverted(restarted)")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "openlist"
    act = sys.argv[2] if len(sys.argv) > 2 else "inject"
    sys.exit(revert(c) if act == "revert" else inject(c))
