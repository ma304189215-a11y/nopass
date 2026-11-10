# Changelog

## 1.0.0

首个可发布版本。

### 核心
- `scan.py`：扫描容器 Web 端口，按「是否对外」决定免密；端口转对外时自动撤销
- `sources.py`：对外端口多来源（gateway_file / uci / iptables / static），带 dest_ip 本机过滤
- `fw-export.py`：网关侧导出器（uci / iptables / pve-vm），支持 --raw 自定义镜像路径
- `fw-sync.sh`：导出 + 推送（local / pct / ssh），配置驱动
- `nopass`：CLI（status / ports / sources / scan / enable / revert / revert-all / log）

### 适配器
- qinglong：注入 index.html，写 localStorage token
- openlist：写官方 customize_head 注入点（前端 Go embed，无 index.html）
- qbittorrent：只读检查自带网段白名单
- nginx_basic：Basic Auth 加私网 allow + satisfy any
- password_login：通用表单登录 → 注入 token/cookie
- workbuddy_manager：用 users.json 的 secret 签会话 cookie

### 可移植
- probe 代理、qinglong token 路径、PVE 镜像路径全部配置化
- install.sh 一键安装（PREFIX 可改）
- 零第三方依赖，全部 Python 标准库
