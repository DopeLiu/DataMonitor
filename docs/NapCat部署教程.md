# NapCat 部署教程（对接 DataMonitor 金价推送）

> NapCat 是基于 **NTQQ** 的无头协议端框架，实现 **OneBot 11** 标准协议。
> 部署后，DataMonitor 通过 HTTP 调用它，即可向 **QQ 群 / QQ 好友** 推送消息——
> 客户只需在 QQ 里接收，无需安装任何额外软件。
>
> ⚠️ **合规与风险提示（务必先读）**：第三方协议端**违反 QQ 用户协议**。2025 年 9 月曾发生公网 OneBot 空 token 攻击事件，导致**大量 QQ 号与群被永久封禁**。请务必：**只用小号**、**HTTP 服务仅监听 127.0.0.1**、**必须设置强 access_token**、**保持低发送频率**。

---

## 一、前置准备

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（x64）、Linux（x64/ARM64）；macOS 需走 Docker |
| QQ 客户端 | **QQNT ≥ 9.9.27** |
| Node.js | 18.x 及以上（LTS 推荐）——使用 Windows「一体化包」则自带，无需另装 |
| 权限 | Windows 需**管理员权限**（涉及注册表查找与注入） |
| QQ 账号 | **准备一个小号**，不要使用主号或客户群群主号 |

---

## 二、Windows 部署（推荐，最省事）

### 步骤 1：下载

注意：现在出了桌面端 NapCatQQ Desktop，操作更方便，在桌面端内可直接下载NapCat，感谢作者

如果你下了桌面端，后续所有操作都可以在桌面端的操作界面完成，再次感谢作者

打开 NapCat 官方仓库 Releases 页面：
`https://github.com/NapNeko/NapCatQQ/releases`

根据情况选择（二选一）：

| 包名 | 适用场景 | 入口 |
|---|---|---|
| `NapCat.Shell.Windows.OneKey.zip` | **一键部署包**（含安装器，最推荐新手） | `NapCatInstaller.exe` → `launcher.bat` |
| `NapCat.Shell.Windows.Node.zip` | 一体化包（自带 QQ 二进制与 Node 运行时） | `launcher.bat` / `napcat.bat` |

### 步骤 2：解压并运行
1. 解压到一个**不含中文和空格**的目录，例如 `D:\napcat`。
2. 若用 OneKey 包：先运行 `NapCatInstaller.exe` 完成安装，再进入 `.shell` 目录。
3. 运行 `launcher.bat`（Win10/11）或 `napcat.bat` 启动。
   - 脚本会自动通过注册表检测 QQ 安装路径。

### 步骤 3：登录 QQ 小号
- 启动后会拉起一个 QQ 登录窗口，**用小号扫码登录**。
- ⚠️ 这个窗口是协议端维持登录态的载体：**可以最小化，但不要退出**。
- 控制台会打印出 WebUI 地址和访问令牌，形如：
  ```
  [WebUi] WebUi User Panel Url: http://127.0.0.1:6099/webui?token=XXXXXXXX
  ```
- 若没记住 token：查看日志文件，或查看 `config/` 下的 WebUI 配置。

---

## 三、开启 OneBot 11 HTTP 服务

### 方式 A：WebUI 图形化配置（推荐）
1. 浏览器打开 `http://127.0.0.1:6099/webui?token=你的token`。
2. 进入 「网络配置」→「对接应用端」→ 点「新增」→ 选 **HTTP 服务器**：
   | 字段 | 建议值 | 说明 |
   |---|---|---|
   | 名称 | `datamonitor` | 任意 |
   | 启用 | ✅ 开启 | 必选 |
   | Host / 地址 | **`127.0.0.1`** | **切勿填 0.0.0.0**（会暴露公网） |
   | Port / 端口 | `3000` | 与程序配置保持一致 |
   | **Token / access_token** | **一段强随机口令（≥16 位）** | **必填！** |
   | 消息格式 | `array` 或 `string` 均可 | 本程序发送纯文本，两者兼容 |
3. 保存并启用。日志出现类似：
   ```
   HTTP服务: 127.0.0.1:3000, 已启动
   ```

### 方式 B：直接改配置文件
编辑 `config/onebot.json`：

```json
{
  "network": {
    "httpServers": [
      {
        "name": "datamonitor",
        "enable": true,
        "host": "127.0.0.1",
        "port": 3000,
        "messagePostFormat": "string",
        "token": "换成你的强随机口令"
      }
    ]
  }
}
```
改完重启 NapCat 生效。

---

## 四、开启 HTTP Webhook（让 QQ 能发指令查价）

第三节的「HTTP 服务器」只解决**发消息**。要让机器人认识 `#查询金价`，还需要再开一个方向相反的通道——**HTTP Webhook**：由 NapCat 把收到的消息主动推给 DataMonitor。

> **命名对照**：新版 WebUI 的「新增」菜单里叫 **HTTP Webhook**；旧版叫「HTTP 上报」或「HTTP 客户端」；配置文件中的键名是 `httpClients`。三者是同一个东西，不要和「HTTP SSE 服务器」混淆——SSE 由 NapCat 开端点、程序连上去长连接订阅，方向与实现都不同。

两者是并列关系，**配了前者不代表后者生效**：

| | HTTP 服务器 | HTTP Webhook |
|---|---|---|
| 谁发起连接 | DataMonitor → NapCat | NapCat → DataMonitor |
| 作用 | 发消息 | 收消息 |
| 端口 | `3000` | `3001` |

### 方式 A：WebUI 配置（推荐）

「网络配置」→「对接应用端」→ 点「新增」→ 选 **HTTP Webhook**：

| 字段 | 建议值 | 说明 |
|---|---|---|
| 名称 | `datamonitor-query` | 任意 |
| 启用 | ✅ 开启 | 不开启则发指令无响应 |
| URL / 上报地址 | `http://127.0.0.1:3001` | 与 `config.json` 的 `query.listen_port` 一致 |
| 消息格式 | `string` | `array` 也兼容 |
| 上报自身消息 | **关闭** | 开启会把机器人自己的回复当成新消息上报，形成自问自答 |
| Token | 留空 | 需要校验时，与 `query.access_token` 保持一致 |

保存后日志应出现：

```
HTTP上报服务: http://127.0.0.1:3001, : 已启动
```

### 方式 B：直接改配置文件

编辑 `config/onebot11_你的QQ号.json`，与 `httpServers` 并列增加：

```json
"httpClients": [{
  "name": "datamonitor-query",
  "enable": true,
  "url": "http://127.0.0.1:3001",
  "messagePostFormat": "string",
  "reportSelfMessage": false,
  "token": "",
  "debug": false
}]
```

改完重启 NapCat 生效。

### 验证

先启动 DataMonitor（`main.py` 或 `gui.py`），日志出现「查询服务已启动：http://127.0.0.1:3001」后，在 QQ 里给机器人发 `#查询金价`，应收到当前金价与告警阈值。

---

## 五、获取目标群号 / 好友 QQ 号

**群号**：在 QQ 里打开目标群 → 群资料页可见群号；或调用 API 列出所有群：
```bash
curl -X POST http://127.0.0.1:3000/get_group_list \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 你的token"
```

**好友**：对方的 QQ 号即可（需先与该小号加为好友）。

**让客户能收到**：把机器人小号**拉进客户群**，或让客户**加该小号为好友**。

---

## 六、连通性验证（接入程序前先自测）

### 1. curl 直接发一条群消息
```bash
curl -X POST http://127.0.0.1:3000/send_group_msg \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 你的token" \
  -d '{"group_id": 你的群号, "message": "NapCat 连通性测试"}'
```
期望返回：`{"status":"ok","retcode":0,...}`，且群里出现消息。

> 说明：本程序使用 OneBot 11 标准端点 `/send_group_msg`、`/send_private_msg`，NapCat、LLOneBot、Lagrange 均支持。

### 2. 接入 DataMonitor
编辑项目 `config.json` 的 `notify.qq`：

```json
"qq": {
  "enabled": true,
  "api_base": "http://127.0.0.1:3000",
  "access_token": "与 NapCat 中填写的一致",
  "timeout_seconds": 10,
  "rate_limit_per_minute": 20,
  "targets": {
    "groups": [123456789],
    "users": [10001]
  }
}
```

然后运行自测脚本：
```bash
python notifier.py
```
看到日志 `✅ 告警已推送到 QQ`，且 QQ 群收到消息，即接入成功。

---

## 七、常见问题

| 现象 | 原因与解决 |
|---|---|
| 返回 **403 Forbidden** | token 不一致或未设置。核对 `config.json` 的 `access_token` 与 NapCat 中完全一致 |
| 返回 **404** / 不支持的 API | 端点不匹配。确认 NapCat 已启用 HTTP 服务、端口正确；或用 `/send_msg` + `"message_type":"group"` 形式 |
| 连接被拒绝 | NapCat 未启动、HTTP 服务未开启、端口被占用或 `api_base` 地址写错 |
| 机器人频繁掉线 | 换更稳定的小号；避免同号多端登录；降低发送频率；勿在机房 IP 登录 |
| 消息发不出且无报错 | 机器人被移出群 / 未加好友 / 群号写错 |
| 发 `#查询金价` 没反应 | ① HTTP Webhook 未开启；② 上报 URL 与 `query.listen_port` 不一致；③ DataMonitor 未运行（该端口由程序监听）。排查：看界面底部「查询服务」状态 → 看 `monitor.log` 有无「查询服务已启动」 |
| 机器人对着自己的消息反复回复 | HTTP Webhook 的「上报自身消息」未关闭（`reportSelfMessage` 必须为 `false`） |

---

## 八、长期稳定运行建议

- **Linux/Docker 更适合常驻**：Windows 上的登录窗口依赖图形界面，服务器上建议用 Docker 部署（见官方 `NapCat-Docker` 仓库的 README，镜像名与参数随版本变化，请以其当前推荐命令为准）。
- **systemd / PM2 守护**：避免进程意外退出；NapCat-Docker 亦支持 `--restart=always`。
- **定期备份** `config/` 目录，避免重装后重新配置。
- **安全三件套**再强调一次：小号、`127.0.0.1`、强 token。

---

## 九、风险兜底

DataMonitor 的推送层与业务逻辑是解耦的：若 NapCat 这条通道日后不可用（封号、掉线等），只需在 `config.json` 中调整或新增通道配置（例如换成短信网关），**业务逻辑无需改动**。
