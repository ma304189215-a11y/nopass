#!/usr/bin/env python3
"""青龙适配器
用法：
  python3 qinglong.py <container>          # 注入免密
  python3 qinglong.py <container> revert   # 撤销免密（恢复原始 index.html）
"""
import os, subprocess, sys

import json as _json
CONF = "/opt/nopass/config.json"


def _tokfile():
    """青龙长期 token 文件位置，可在 config.json 的 qinglong_token_file 覆盖"""
    try:
        return _json.load(open(CONF)).get("qinglong_token_file") or "/opt/ql/.ql_nopass_token"
    except Exception:
        return "/opt/ql/.ql_nopass_token"
MARK = "QL_NOPASS"
INNER = "/ql/static/dist/index.html"


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def revert(container):
    r = sh(["docker", "exec", container, "sh", "-c",
            "test -f %s.bak && cp -f %s.bak %s && echo reverted || echo no-backup"
            % (INNER, INNER, INNER)])
    out = (r.stdout or "").strip().splitlines()
    print(out[-1] if out else "revert-fail")
    return 0


def inject(container):
    tf = _tokfile()
    if not os.path.exists(tf):
        print("no-token:%s" % tf)
        return 1
    tok = open(tf).read().strip()
    if not tok:
        print("empty-token")
        return 1

    r = sh(["docker", "exec", container, "cat", INNER])
    if r.returncode != 0:
        print("read-fail")
        return 1
    html = r.stdout
    if MARK in html:
        print("already")
        return 0

    inj = ('<script>/*QL_NOPASS*/(function(){var h=location.hostname;'
           'if(/^(localhost|127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)/.test(h))'
           '{localStorage.setItem("token","' + tok + '");}})();</script>')
    new = html.replace("<head>", "<head>" + inj, 1) if "<head>" in html else inj + html

    sh(["docker", "exec", container, "sh", "-c",
        "test -f %s.bak || cp -p %s %s.bak" % (INNER, INNER, INNER)])
    p = sh(["docker", "exec", "-i", container, "sh", "-c", "cat > %s" % INNER], input=new)
    if p.returncode != 0:
        print("write-fail")
        return 1
    print("injected")
    return 0


def main():
    container = sys.argv[1] if len(sys.argv) > 1 else "qinglong"
    action = sys.argv[2] if len(sys.argv) > 2 else "inject"
    return revert(container) if action == "revert" else inject(container)


if __name__ == "__main__":
    sys.exit(main())
