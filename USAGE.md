# nopass 使用手册

面向实际部署，从零到跑通。假设你已经把仓库或发布包拿到手。

---

## 一、它解决什么问题

家里/公司内网一堆自建服务（青龙、OpenList、qBittorrent……），每个都要登录。
但**只要这个端口没对公网开放，登录其实只是自找麻烦**。

nopass 做两件事：

1. 端口**没**对外开放 → 自动免密，打开就进
2. 端口**对外**开放 → 自动撤销免密，恢复登录页

判断依据不是"我觉得"，而是**网关的端口转发表**。

---

## 二、三种部署形态

先搞清楚你的服务在哪、网关在哪，再选形态。

### 形态 A：单机（服务和网关是同一台）

适合：OpenWrt 上跑 docker、或服务直接跑在软路由上。

```sh
sudo ./install.sh                      # 装到 /opt/nopass
nopass sources                         # 看能读到哪些来源
python3 /opt/nopass/fw-export.py --source uci --out /opt/nopass/state/gateway-ports.json
nopass scan && nopass status
```

cron 加两条（`install.sh` 已自动加第一条）：

```sh
* * * * *  /usr/bin/python3 /opt/nopass/scan.py
*/5 * * * * cd /opt/nopass && python3 fw-export.py --source uci --out state/gateway-ports.json
```

### 形态 B：网关与服务分离（本文实测环境）

适合：PVE 上跑 LXC 容器，网关是另一台 OpenWrt 虚拟机。

```
PVE 宿主(99)              LXC 容器(98)              网关 VM(96)
─────────────             ─────────────            ──────────
fw-sync.sh                scan.py                   /etc/config/firewall
  ↓ 只读挂载 VM 磁盘         ↓ 读 gateway-ports.json      ↑
  解析 firewall DNAT   →→   判断哪些端口对外              解析
```

**网关侧（PVE 宿主）**：

```sh
cp fw-export.py fw-sync.sh /root/ && chmod +x /root/fw-sync.sh
cat > /root/gateway-sync.conf <<EOF
MODE=pve-vm
VMID=102
PUSH=pct:100
PUSH_TARGET=/opt/nopass/state/gateway-ports.json
EOF
/root/fw-sync.sh                       # 手动试一次
echo '*/5 * * * * /root/fw-sync.sh >/dev/null 2>&1' | crontab -
```

**服务侧（LXC 容器）**：

```sh
sudo ./install.sh
echo '* * * * * /usr/bin/python3 /opt/nopass/scan.py >/dev/null 2>&1' | crontab -
```

### 形态 C：网关是普通 Linux

```sh
MODE=iptables
```

`fw-export.py --source iptables` 会解析 `nat` 表的 `PREROUTING` DNAT 规则。

---

## 三、`gateway-sync.conf` 全部字段

```sh
MODE=pve-vm            # uci | iptables | pve-vm
VMID=102               # MODE=pve-vm 时必填
PUSH=pct:100           # local | pct:<容器ID> | ssh:<user@host>
PUSH_TARGET=/opt/nopass/state/gateway-ports.json
EXPORT_BIN=/root/fw-export.py    # 可选，自动探测
```

非默认存储池（`local-lvm` 等）要指定裸盘路径：

```sh
/root/fw-sync.sh   # 或用环境变量
python3 fw-export.py --source pve-vm --vmid 102 \
  --raw /dev/pve/vm-102-disk-0 --out gateway-ports.json
```

---

## 四、配置 `config.json`

```json
{
  "public_sources": ["gateway_file", "uci", "iptables", "static"],
  "gateway_ports_file": "/opt/nopass/state/gateway-ports.json",
  "public_ports": [],
  "auto_probe": false,
  "probe_proxy": "",
  "probe_hostname": "",
  "qinglong_token_file": "/opt/ql/.ql_nopass_token",
  "known": {},
  "image_rules": {}
}
```

| 字段 | 作用 |
|---|---|
| `public_sources` | 按顺序取并集，哪个能给结果就用哪个 |
| `public_ports` | 手工强制标为对外的端口，优先级最高 |
| `auto_probe` | 默认 `false`。开了会用代理实测，**不推荐**（依赖代理出口，容易误判） |
| `probe_proxy` | 仅 `auto_probe` 用。出口必须是公网，才能代表外部视角 |
| `probe_hostname` | 仅 `auto_probe` 用，解析 AAAA 做探测目标 |
| `known` | 容器名 → 适配器（精确匹配，优先级最高） |
| `image_rules` | 镜像名片段 → 适配器（同族镜像自动套用） |

### 为什么默认关掉 auto_probe

实测过：`8788` 和 `7863` 在网关是 **DNAT 到 98** 的，但从另一台机器走代理探测时探不到
（出口路径不同），会误判成"不对外"，于是错误地启用了免密。

**读网关配置才是权威**。探测只作为网关读不到时的兜底。

---

## 五、适配器

每个适配器实现两个动作：

```sh
python3 adapters/<name>.py <容器名>          # 注入免密
python3 adapters/<name>.py <容器名> revert   # 撤销
```

退出码 `0` 表示成功，stdout 最后一行写进日志。

### 现有 6 个

| 适配器 | 适用 | 原理 | 注意 |
|---|---|---|---|
| `qinglong` | 青龙面板 | 注入 `index.html`，写 localStorage token | 需要长期 JWT，写 DB + keyv 两处 |
| `openlist` | OpenList / AList | 写官方「自定义头部」`customize_head` | 前端 Go embed，无 index.html；改完要重启 |
| `qbittorrent` | qBittorrent | **只读检查**自带网段白名单 | 运行时会内存覆盖配置文件，写文件无效 |
| `nginx_basic` | Basic Auth 类 | nginx 加 `satisfy any` + 私网 `allow` | 外网仍需密码，比关认证安全 |
| `password_login` | 通用表单登录 | 账号密码登录 → 注入 token/cookie | 凭据放 `secrets.json` |
| `workbuddy_manager` | workbuddy-manager | 用 `users.json` 的 secret 签会话 cookie | httponly 限制见下 |

### `workbuddy_manager` 的 httponly 限制

会话 cookie 是 `httponly`，JS 无法覆盖同名 cookie。注入脚本只在**浏览器里从无该 cookie**
（新设备 / 清过缓存）时生效；已登录过的浏览器要手动清一次。

### 新增一个适配器

1. 复制最接近的那个，改成目标应用的注入方式
2. 在 `config.json` 的 `known` 登记：

```json
"容器名": { "adapter": "适配器文件名", "port": 端口 }
```

3. 同类镜像换容器名，只加 `image_rules` 一行：

```json
"image_rules": { "厂商/镜像名": "适配器名" }
```

---

## 六、凭据 `secrets.json`

```json
{
  "openlist": {
    "url": "http://127.0.0.1:5244",
    "username": "admin",
    "password": "你的密码"
  }
}
```

- 权限必须 `600`
- `password` 留空 → 该服务跳过免密，**不会去猜密码、不会改用户密码**
- 已在 `.gitignore` 里，不会被提交

---

## 七、命令行

```sh
nopass status            # 服务状态表
nopass ports             # 当前认定的对外端口 + 来源 + DNAT 目标
nopass sources           # 各来源可用性
nopass scan              # 立即扫描一次
nopass enable <服务>     # 立即对某服务注入
nopass revert <服务>     # 撤销某服务
nopass revert-all        # 撤销全部
nopass log [N]           # 变更日志
```

---

## 八、排查

### 服务一直是 `no-adapter`

说明扫到了但没配适配器。看 `nopass status` 的端口对不对，再决定加 `known` 还是 `image_rules`。

### 服务是 `skipped-public`

端口被判定为对外。用 `nopass ports` 看它从哪个来源、DNAT 目标是谁。

**注意**：只有 DNAT 的 `dest_ip` 命中**本机地址**才算本机对外。指向别的内网机的不算。

### 注入了但页面没变化

应用有内存缓存。`openlist` 改完要重启容器（适配器已自动做）。其他应用同理。

### 代理不可用导致全判"不对外"

`auto_probe` 关掉就用不到代理。关掉后只看网关配置。

---

## 九、安全边界

| 项 | 说明 |
|---|---|
| 生效范围 | 只在**私网 hostname** 免密（`127.` / `10.` / `192.168.` / `172.16-31.`） |
| 鉴权是否拆掉 | **没有**。无 token 直接调 API 仍返回 401 |
| 外网访问 | hostname 不匹配 → 不注入 → 照常走登录页 |
| 改动可回滚 | 所有适配器都留 `.nopassbak`，`nopass revert` 一键还原 |
| 密码处理 | 只读不写。不猜密码、不改用户密码 |

**前提**：本工具假设你的服务端口**没有**对公网开放。如果某天你把端口转发出去，
nopass 会在下一轮扫描时自动撤销免密——但别把安全全押在这上面，转发前先确认服务本身有认证。
