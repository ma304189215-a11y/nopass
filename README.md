# nopass —— 内网服务自动免密（可移植）

> 完整部署与排查见 **[USAGE.md](USAGE.md)**

对「**没有对外开放**」的 Web 服务自动免密；端口一旦对外开放，自动撤销免密并恢复登录页。
判定依据是**网关/主路由的端口转发规则**，不依赖任何代理。

## 一、原理

```
网关端口转发规则 ──► 对外端口清单 ──► 在清单内  → 不做免密（已注入的会被撤销）
                                    └─ 不在清单内 → 免密生效
```

`scan.py` 每分钟扫一次本机容器，只认「对外端口清单」这个结果，不关心它从哪来。

## 依赖

**零第三方依赖**，全部使用 Python 标准库（`json` / `os` / `re` / `socket` / `sqlite3` /
`subprocess` / `urllib` / `hashlib` / `hmac` / `base64` / `concurrent.futures` / `argparse`）。

运行环境要求：

| 组件 | 是否必需 | 说明 |
|---|---|---|
| Python ≥ 3.8 | 必需 | 所有脚本 |
| Docker CLI | 扫描容器时需要 | `scan.py` 用 `docker ps` 发现服务 |
| curl | 可选 | `probe.py` 兜底探测 |
| iptables / uci | 可选 | `sources.py` 的对应来源 |
| pct / ssh | 可选 | `fw-sync.sh` 的推送方式 |

`requirements.txt` 里只有注释——**不需要 pip install 任何东西**。

## 二、目录

```
nopass/
├── nopass            命令行：status / scan / ports / sources / revert / revert-all / enable / log
├── scan.py           总控（cron 每分钟）
├── sources.py        对外端口来源：gateway_file / uci / iptables / static
├── fw-export.py      网关侧导出器：uci / iptables / pve-vm
├── fw-sync.sh        同步器：导出 → 推送到服务机（local / pct / ssh）
├── probe.py          代理探测（兜底，默认关）
├── install.sh        一键安装
├── requirements.txt  依赖声明（空，纯标准库）
├── secrets.example.json  凭据模板
├── VERSION / CHANGELOG.md / LICENSE
├── config.json       配置
├── secrets.json      各服务登录凭据（600）
├── adapters/         每个应用一个免密适配器
├── state/            scan.json（服务状态）、gateway-ports.json（对外端口）
└── log/scan.log
```

## 三、部署矩阵（对外端口从哪来）

| 场景 | MODE | 怎么跑 |
|---|---|---|
| 网关是独立机器（PVE 上的 VM / 软路由） | `pve-vm` | 在 PVE 宿主跑，`VMID=102` |
| 就在主路由上跑（OpenWrt） | `uci` | 直接读 `/etc/config/firewall` |
| 任意 Linux 网关 | `iptables` | 解析 `nat PREROUTING` 的 DNAT |
| 纯手工 | `static` | 写 `config.json` 的 `public_ports` |

`fw-sync.sh` 的配置写在 `gateway-sync.conf`：

```sh
MODE=pve-vm
VMID=102
PUSH=pct:100                                    # 或 local / ssh:user@host
PUSH_TARGET=/opt/nopass/state/gateway-ports.json
```

## 四、部署步骤

### A. 主路由就是本机（OpenWrt）

```sh
mkdir -p /opt/nopass && cd /opt/nopass        # 解包
python3 fw-export.py --source uci --out state/gateway-ports.json
echo '* * * * * /usr/bin/python3 /opt/nopass/scan.py >/dev/null 2>&1' >> /etc/crontabs/root
echo '*/5 * * * * cd /opt/nopass && /usr/bin/python3 fw-export.py --source uci --out state/gateway-ports.json >/dev/null 2>&1' >> /etc/crontabs/root
/etc/init.d/cron restart
```

### B. 网关与服务分离（PVE + LXC）

```sh
# 网关侧（PVE 宿主）：每 5 分钟导出并推送
cp fw-export.py /root/ && cp fw-sync.sh /root/ && chmod +x /root/fw-sync.sh
cat > /root/gateway-sync.conf <<EOF
MODE=pve-vm
VMID=102
PUSH=pct:100
PUSH_TARGET=/opt/nopass/state/gateway-ports.json
EOF
echo '*/5 * * * * /root/fw-sync.sh >/dev/null 2>&1' | crontab -

# 服务侧（LXC）：
echo '* * * * * /usr/bin/python3 /opt/nopass/scan.py >/dev/null 2>&1' | crontab -
```

## 五、已实现的适配器

| 适配器 | 适用 | 做法 | 特点 |
|---|---|---|---|
| `qinglong.py` | 青龙面板 | 注入 index.html，写 localStorage token | 需长期 JWT（写 DB + keyv） |
| `nginx_basic.py` | Basic Auth 类（prompt-optimizer） | nginx 加 `satisfy any` + 私网 allow | 不动密码，外网仍要密码 |
| `qbittorrent.py` | qBittorrent | **只读检查**自带网段白名单 | 天然免密，不写文件 |
| `openlist.py` | OpenList / AList | 走登录拿真 token → 写 `customize_head` | 前端是 Go embed，无 index.html |
| `password_login.py` | 通用表单登录类 | 账号密码登录 → 注入 token/cookie | 凭据放 `secrets.json` |
| `workbuddy_manager.py` | workbuddy-manager | 用 secret 签会话 cookie → 注入 | httponly 限制见下 |

**已知限制**：`workbuddy-manager` 的会话是 `httponly`，JS 无法覆盖同名 cookie。
注入脚本只对「浏览器里从无该 cookie」（新设备/清过缓存）生效；
已登录过的浏览器需手动清一次 cookie。

## 六、命令行

```sh
nopass status            # 所有服务状态
nopass ports             # 当前对外端口 + 来源
nopass sources           # 各来源可用性
nopass scan              # 立即扫描
nopass enable <服务>     # 立即对某服务注入免密
nopass revert <服务>     # 撤销某服务免密
nopass revert-all        # 撤销全部
nopass log [N]           # 变更日志
```

## 七、新增一个服务的免密

1. 复制最接近的适配器，改注入方式
2. `config.json` 的 `known` 加：
```json
"容器名": { "adapter": "适配器文件名", "port": 端口 }
```
3. 同类镜像换容器名：只加 `image_rules` 一行：
```json
"image_rules": { "厂商/镜像名": "适配器名" }
```

**适配器接口**：
```sh
python3 adapters/xxx.py <container>          # 注入
python3 adapters/xxx.py <container> revert   # 撤销
```
退出码 0 = 成功；stdout 最后一行写进日志。

## 八、关键字段

```json
{
  "public_sources": ["gateway_file", "uci", "iptables", "static"],
  "gateway_ports_file": "/opt/nopass/state/gateway-ports.json",
  "public_ports": [],
  "auto_probe": false,
  "known": {},
  "image_rules": {}
}
```

`sources.py` 里有一层必要过滤：**DNAT 的 dest_ip 必须命中本机地址**，
否则「网关把 8788 转给别的内网机」会被误判成本机 8788 对外。

## 九、安全边界

- 免密只在**私网 hostname** 生效（127./10./192.168./172.16-31.）
- 鉴权本身没拆：无 token 直接调 API 仍然 401
- 所有改动都留 `.nopassbak`
- `secrets.json` 权限 600，密码留空则该服务跳过免密（不猜密码、不改用户密码）
