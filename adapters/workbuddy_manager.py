#!/usr/bin/env python3
"""workbuddy-manager 适配器

会话是 **httponly** cookie：JS 无法覆盖同名 cookie，所以「注入 localStorage/cookie」
这条常规路不可靠。但它的签名算法是 HMAC-SHA256，secret 在容器内 users.json 里，
可以合法签出一个会话 cookie。

两种落地方式（按可靠度）：
  A. inject-file  —— 注入 index.html，由 JS 设置同名 **非 httponly** cookie。
     仅对「浏览器里从无该 cookie」（新设备/清过缓存）生效。
  B. header-hint  —— 走本适配器的 `check` 模式，只报告是否可签，
     实际使用建议在浏览器里登录一次（服务端 httponly 会长期有效，30 天）。

用法：
  workbuddy_manager.py <container> [inject|revert|check]
"""
import base64, hashlib, hmac, json, re, subprocess, sys, time

COOKIE_NAME = "wb_session"
MARK = "WB_NOPASS"


def sh(a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def find_html(c):
    for p in ("/app/web/out/index.html", "/app/static/index.html",
              "/app/dist/index.html", "/app/public/index.html"):
        r = sh(["docker", "exec", c, "sh", "-c", "test -f %s && echo ok" % p])
        if (r.stdout or "").strip() == "ok":
            return p
    return None


def make_cookie(c):
    r = sh(["docker", "exec", c, "cat", "/app/data/users.json"])
    if r.returncode != 0:
        return None, "no-users-json"
    d = json.loads(r.stdout)
    secret, users = d.get("secret"), d.get("users") or []
    if not secret or not users:
        return None, "no-secret"
    u = users[0]
    now = int(time.time())
    payload = json.dumps({"username": u.get("username"), "role": u.get("role", ""),
                          "sv": int(u.get("sv") or 0), "iat": now,
                          "orig": now, "exp": now + 30 * 86400})
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(("%s|%s" % (payload, sig)).encode()).decode(), None


def check(c):
    tok, err = make_cookie(c)
    if err:
        print(err)
        return 1
    p = find_html(c)
    print("cookie-ok len=%d; index=%s" % (len(tok), p or "not-found"))
    return 0


def inject(c):
    p = find_html(c)
    if not p:
        print("no-index")
        return 1
    tok, err = make_cookie(c)
    if err:
        print(err)
        return 1
    r = sh(["docker", "exec", c, "cat", p])
    if r.returncode != 0:
        print("read-fail")
        return 1
    html = r.stdout
    if MARK in html:
        print("already")
        return 0
    inj = ('<script>/*%s*/(function(){var h=location.hostname;'
           'if(!/^(localhost|127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)/.test(h))return;'
           'if(document.cookie.indexOf("%s=")<0){document.cookie="%s=%s; path=/; max-age=2592000";}})();</script>'
           % (MARK, COOKIE_NAME, COOKIE_NAME, tok))
    new = html.replace("<head>", "<head>" + inj, 1) if "<head>" in html else inj + html
    sh(["docker", "exec", c, "sh", "-c",
        "test -f %s.nopassbak || cp -p %s %s.nopassbak" % (p, p, p)])
    if sh(["docker", "exec", "-i", c, "sh", "-c", "cat > %s" % p], input=new).returncode != 0:
        print("write-fail")
        return 1
    print("injected")
    return 0


def revert(c):
    p = find_html(c)
    if not p:
        print("no-index")
        return 1
    r = sh(["docker", "exec", c, "sh", "-c",
            "test -f %s.nopassbak && cp -f %s.nopassbak %s && echo reverted || echo no-backup"
            % (p, p, p)])
    out = (r.stdout or "").strip().splitlines()
    print(out[-1] if out else "revert-fail")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "workbuddy-manager"
    act = sys.argv[2] if len(sys.argv) > 2 else "inject"
    if act == "revert":
        sys.exit(revert(c))
    if act == "check":
        sys.exit(check(c))
    sys.exit(inject(c))
