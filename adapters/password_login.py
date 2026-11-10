#!/usr/bin/env python3
"""通用「账号密码登录 → 注入」适配器

适用于自己就能登录、只是懒得每次输密码的应用（openlist / workbuddy-manager 等）。
不伪造凭据、不破解签名——真的走一次它自己的登录接口，拿真实 token/cookie。

凭据从 /opt/nopass/secrets.json 读，格式：
{
  "openlist": {"type": "bearer", "url": "http://127.0.0.1:5244/api/auth/login",
               "username": "admin", "password": "xxx",
               "token_path": "data.token", "header": "Authorization",
               "storage_key": "token"},
  "myapp":    {"type": "cookie", "url": "http://127.0.0.1:8080/login",
               "username": "u", "password": "p",
               "cookie_name": "sid"}
}

注入方式：在容器内 index.html 的 <head> 里插一段脚本，
把 token 写进 localStorage（type=bearer）或 document.cookie（type=cookie）。
只在私网 hostname 生效。

用法：
  password_login.py <container>         注入
  password_login.py <container> revert  撤销
"""
import json, os, re, subprocess, sys, urllib.request

SECRETS = "/opt/nopass/secrets.json"
CFG = "/opt/nopass/config.json"
MARK = "NOPASS_LOGIN"
HTML_CANDIDATES = ("/app/web/out/index.html", "/app/static/index.html",
                   "/ql/static/dist/index.html", "/app/dist/index.html",
                   "/opt/openlist/data/index.html", "/usr/share/nginx/html/index.html")


def sh(a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def detect_container_ip(c):
    r = sh(["docker", "inspect", c, "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"])
    return (r.stdout or "").strip()


def get_secret(name):
    try:
        return json.load(open(SECRETS)).get(name)
    except Exception:
        return None


def login(spec, host):
    url = re.sub(r'//127\.0\.0\.1(?=[:/])', '//%s' % host, spec["url"]) \
        if "127.0.0.1" in spec["url"] else spec["url"]
    body = json.dumps({"username": spec["username"], "password": spec["password"]}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=10)
    data = json.loads(r.read() or b"{}")
    if spec.get("type") == "cookie":
        sc = r.headers.get("Set-Cookie", "")
        m = re.search(r'%s=([^;]+)' % re.escape(spec.get("cookie_name", "sid")), sc)
        return ("cookie", m.group(1)) if m else (None, None)
    # bearer：按 token_path 取值
    cur = data
    for k in spec.get("token_path", "data.token").split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and k.isdigit():
            cur = cur[int(k)]
        else:
            return (None, None)
    return ("bearer", cur) if isinstance(cur, str) and cur else (None, None)


def find_html(c):
    for p in HTML_CANDIDATES:
        r = sh(["docker", "exec", c, "sh", "-c", "test -f %s && echo ok" % p])
        if (r.stdout or "").strip() == "ok":
            return p
    return None


def build_script(kind, value, spec):
    guard = ('var h=location.hostname;'
             'if(!/^(localhost|127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)/.test(h))return;')
    if kind == "bearer":
        key = spec.get("storage_key", "token")
        return ('<script>/*%s*/(function(){%s'
                'localStorage.setItem(%s,%s);})();</script>'
                % (MARK, guard, json.dumps(key), json.dumps(value)))
    return ('<script>/*%s*/(function(){%s'
            'document.cookie=%s;})();</script>'
            % (MARK, guard,
               json.dumps("%s=%s; path=/; max-age=2592000"
                          % (spec.get("cookie_name", "sid"), value))))


def inject(c, name):
    spec = get_secret(name)
    if not spec:
        print("no-secret")
        return 1
    html_path = find_html(c)
    if not html_path:
        print("no-index")
        return 1
    host = detect_container_ip(c) or "127.0.0.1"
    try:
        kind, value = login(spec, host)
    except Exception as e:
        print("login-fail:%s" % str(e)[:80])
        return 1
    if not value:
        print("empty-token")
        return 1

    r = sh(["docker", "exec", c, "cat", html_path])
    if r.returncode != 0:
        print("read-fail")
        return 1
    html = r.stdout
    script = build_script(kind, value, spec)

    if MARK in html:
        html = re.sub(r'<script>/\*%s\*/.*?</script>' % MARK, "", html, flags=re.S)
    new = html.replace("<head>", "<head>" + script, 1) if "<head>" in html else script + html

    sh(["docker", "exec", c, "sh", "-c",
        "test -f %s.nopassbak || cp -p %s %s.nopassbak" % (html_path, html_path, html_path)])
    if sh(["docker", "exec", "-i", c, "sh", "-c", "cat > %s" % html_path], input=new).returncode != 0:
        print("write-fail")
        return 1
    print("injected(%s)" % kind)
    return 0


def revert(c):
    html_path = find_html(c)
    if not html_path:
        print("no-index")
        return 1
    r = sh(["docker", "exec", c, "sh", "-c",
            "test -f %s.nopassbak && cp -f %s.nopassbak %s && echo reverted || echo no-backup"
            % (html_path, html_path, html_path)])
    out = (r.stdout or "").strip().splitlines()
    print(out[-1] if out else "revert-fail")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    act = sys.argv[2] if len(sys.argv) > 2 else "inject"
    sys.exit(revert(c) if act == "revert" else inject(c, c))
