# ESP32 设备接入与绑定协议

本文说明摇尾巴小熊猫、爬行大熊猫和互动恐龙如何接入 Audio Hub，重点说明每一步由谁发送、谁接收、谁响应。

## 1. 先分清三个角色

| 角色 | 运行位置 | 主要职责 |
| --- | --- | --- |
| 管理员浏览器 | 管理员的电脑或手机 | 登录后台、输入六位激活码、管理设备和音频 |
| Audio Hub 服务端 | 服务器电脑，目前为 `192.168.1.7:5000` | 监听 HTTP 请求、保存管理员和设备资料、校验令牌、返回音频清单 |
| ESP32 设备端 | 三款玩具的 ESP32-S3 固件 | 主动注册、展示激活码、轮询绑定结果、保存令牌、发送心跳和下载音频 |

最重要的网络规则：

- Audio Hub 服务端是 HTTP 服务端，负责监听 `5000` 端口。
- ESP32 是 HTTP 客户端，由 ESP32 主动连接 Audio Hub。
- 管理员浏览器也是 HTTP 客户端，由浏览器主动连接 Audio Hub。
- 正常的注册、激活、心跳和音频同步过程中，Audio Hub 不会主动连接 ESP32。
- 只有后台的“Flash 工具”属于例外：管理员发出操作后，Audio Hub 会代理访问 ESP32 自己的局域网 HTTP 接口。

```text
管理员浏览器 ──HTTP 请求──> Audio Hub 服务端 <──HTTP 请求── ESP32
管理员浏览器 <──HTTP 响应── Audio Hub 服务端 ──HTTP 响应──> ESP32
```

ESP32 与管理员浏览器之间不直接通信，二者都通过 Audio Hub 完成绑定。

## 2. 完整绑定时序

```text
ESP32 设备端                   Audio Hub 服务端                 管理员浏览器
    │                                │                              │
    │ ① POST /api/device/register    │                              │
    │ 发送设备ID、产品ID、固件版本 ──>│                              │
    │                                │ 创建 pending 设备记录         │
    │<── 201 激活码 + Claim Token ───│                              │
    │ 保存 Claim Token 到 NVS        │                              │
    │ 串口显示六位激活码              │                              │
    │                                │                              │
    │ ② POST /api/device/activate    │                              │
    │ 使用 Claim Token 轮询 ─────────>│                              │
    │<── 200 status=pending ─────────│                              │
    │                                │                              │
    │                                │<── ③ 输入六位激活码 ─────────│
    │                                │ 校验激活码并改为 active       │
    │                                │── 200 绑定成功 ──────────────>│
    │                                │                              │
    │ ④ POST /api/device/activate    │                              │
    │ 再次使用 Claim Token 轮询 ─────>│                              │
    │                                │ 生成正式设备 API Token        │
    │<── 200 API Token ──────────────│                              │
    │ 保存 API Token 到 NVS          │                              │
    │ 删除本地 Claim Token           │                              │
    │                                │                              │
    │ ⑤ POST /api/device/v1/check-in │                              │
    │ Authorization: Bearer Token ───>│ 更新最后在线时间              │
    │<── 200 心跳间隔、音频版本 ─────│                              │
    │                                │── 后台显示设备在线 ──────────>│
    │                                │                              │
    │ ⑥ GET /api/device/v1/files     │                              │
    │ Authorization: Bearer Token ───>│ 根据令牌识别产品型号           │
    │<── 200 该产品的音频清单 ───────│                              │
```

要点：

1. ESP32 注册后不会立即获得正式设备令牌。
2. 管理员输入六位码，只是把服务端设备状态从 `pending` 改为 `active`。
3. 管理员激活成功后，仍然由 ESP32 下一次轮询领取正式设备令牌。
4. 在 ESP32 首次心跳成功前，同一 Claim Token 可以重试领取同一个正式令牌，避免响应丢包导致设备无法恢复。
5. 服务端数据库只保存令牌哈希，不保存正式令牌明文。
5. 之后所有心跳和受保护的音频请求都由 ESP32 携带正式令牌。

## 3. 服务地址和产品身份

当前局域网服务端：

```text
http://192.168.1.7:5000
```

ESP32 的 `main/config.h` 需要配置：

```c
#define OFFLINE_DEMO       0
#define SYNC_SERVER_IP     "192.168.1.7"
#define SYNC_SERVER_PORT   5000
```

`OFFLINE_DEMO=1` 时固件完全跳过 Wi-Fi，设备不会注册、不会发送心跳，也不会从服务端同步音频。

三款产品的产品 ID：

| ESP32 固件工程 | 固件内的 `SYNC_PRODUCT_ID` |
| --- | --- |
| `tailRedPanda` | `tail-wagging-panda` |
| `crawlPanda` | `crawling-panda` |
| `dinosaur` | `dinosaur` |

设备 ID 由 ESP32 端根据 Wi-Fi STA MAC 生成：

```text
ZYLZ-AABBCCDDEEFF
```

要求：

- 每台实体设备永久唯一。
- 长度为 4–64 个字符。
- 只使用字母、数字、冒号、点、下划线或连字符。
- `product_id` 固定编译到对应固件中，不允许用户随意选择。

## 4. 第一步：ESP32 向服务端注册

### 谁发送、谁响应

| 项目 | 内容 |
| --- | --- |
| 发送方 | ESP32 |
| 接收方 | Audio Hub 服务端 |
| 响应方 | Audio Hub 服务端 |
| 触发条件 | ESP32 已连接 Wi-Fi，NVS 中没有正式 `api_token`，也没有待激活的 `claim_token` |

### ESP32 发送

```http
POST http://192.168.1.7:5000/api/device/register
Content-Type: application/json
```

```json
{
  "device_id": "ZYLZ-AABBCCDDEEFF",
  "product_id": "tail-wagging-panda",
  "firmware_version": "1.0.0"
}
```

### 服务端处理

服务端执行以下动作：

1. 检查 `device_id` 格式。
2. 检查 `product_id` 是否为三种有效产品之一。
3. 在 SQLite `devices` 表中创建 `pending` 设备。
4. 生成六位 `activation_code`。
5. 生成仅供该设备轮询使用的 `claim_token`。
6. 保存 Claim Token 的哈希，不保存明文。

### 服务端响应 ESP32

首次注册成功返回 HTTP `201`：

```json
{
  "status": "pending",
  "device_id": "ZYLZ-AABBCCDDEEFF",
  "activation_code": "381204",
  "expires_at": "2026-07-31T09:30:00+00:00",
  "claim_token": "claim_...",
  "poll_after": 3
}
```

### ESP32 收到后必须做什么

ESP32 负责：

1. 立即把 `claim_token` 写入 NVS 的 `claim_token` 键。
2. 把六位码写入 NVS 的 `act_code` 键，以便重启后继续显示。
3. 在串口中显示六位 `activation_code`。
4. 每隔 `poll_after` 秒调用激活查询接口。

ESP32 不得在串口、屏幕或普通日志中显示 Claim Token。

六位激活码默认 30 分钟失效。失效后服务端返回 HTTP `410`，ESP32 删除旧 Claim Token，再重新注册领取新激活码。

## 5. 第二步：ESP32 轮询绑定状态

### 谁发送、谁响应

| 项目 | 内容 |
| --- | --- |
| 发送方 | ESP32 |
| 接收方 | Audio Hub 服务端 |
| 响应方 | Audio Hub 服务端 |
| 触发条件 | ESP32 已注册，NVS 有 `claim_token`，但还没有正式 `api_token` |

### ESP32 发送

```http
POST http://192.168.1.7:5000/api/device/activate
Content-Type: application/json
```

```json
{
  "device_id": "ZYLZ-AABBCCDDEEFF",
  "claim_token": "claim_..."
}
```

### 管理员尚未绑定时，服务端响应

HTTP `200`：

```json
{
  "status": "pending",
  "poll_after": 3
}
```

这表示请求正常，但管理员还没有输入激活码。ESP32 等待 3 秒后再次发送同一个请求。

## 6. 第三步：管理员向服务端提交六位码

### 谁发送、谁响应

| 项目 | 内容 |
| --- | --- |
| 发送方 | 管理员浏览器 |
| 接收方 | Audio Hub 服务端 |
| 响应方 | Audio Hub 服务端 |
| ESP32 是否参与本次请求 | 不参与；ESP32 仍在后台轮询 |

管理员先登录 Audio Hub，然后在“设备管理 → 激活码绑定”中输入设备串口显示的六位码。

浏览器发送：

```http
POST /api/admin/devices/activate
Cookie: 管理员登录会话
X-CSRF-Token: 管理页面中的 CSRF Token
Content-Type: application/json
```

```json
{
  "activation_code": "381204"
}
```

服务端收到后：

1. 查找对应的 `pending` 设备。
2. 检查激活码是否过期。
3. 把设备状态改为 `active`。
4. 清除服务端保存的六位激活码。
5. 向管理员浏览器返回绑定成功。

此时服务端还没有把正式设备令牌发送给 ESP32，因为当前请求来自管理员浏览器，不是 ESP32。

## 7. 第四步：ESP32 领取正式设备令牌

管理员绑定后，ESP32 继续发送第 5 节的 `/api/device/activate` 请求。

服务端确认设备已经是 `active` 后：

1. 生成正式 `api_token`。
2. 数据库只保存 API Token 的 SHA-256 哈希和前缀。
3. 暂时保留 Claim Token 哈希，允许网络响应丢失时重新领取同一个 API Token。
4. 在 HTTP 响应中把 API Token 明文发给 ESP32。

服务端响应：

```json
{
  "status": "active",
  "product_id": "tail-wagging-panda",
  "api_token": "zh_..."
}
```

ESP32 收到后必须按以下顺序处理：

1. 先把 `api_token` 写入 NVS 的 `api_token` 键。
2. 检查 NVS 提交是否成功。
3. 成功后再删除 NVS 中的 `claim_token` 和 `act_code`。
4. 结束激活轮询，立即发送第一次正式心跳。

服务端收到第一次使用 API Token 的有效心跳后，才会清除 Claim Token 哈希。此后旧 Claim Token 失效，不能再次领取令牌。

如果 API Token 响应丢失或写入 NVS 失败，ESP32 不应删除 Claim Token，而应继续调用激活查询接口；服务端会返回同一个 API Token。

正式令牌明文无法从服务端数据库恢复。令牌丢失时，需要管理员在后台重新生成令牌并重新配置设备，或者删除该设备后让它重新走绑定流程。

## 8. 第五步：ESP32 发送心跳

### 谁发送、谁响应

| 项目 | 内容 |
| --- | --- |
| 发送方 | ESP32 |
| 接收方 | Audio Hub 服务端 |
| 响应方 | Audio Hub 服务端 |
| 发送频率 | 设备绑定后立即发送一次，之后每 60 秒一次 |

### ESP32 发送

```http
POST http://192.168.1.7:5000/api/device/v1/check-in
Authorization: Bearer zh_...
Content-Type: application/json
```

```json
{
  "firmware_version": "1.0.0",
  "battery_level": 82,
  "flash_free": 1572864
}
```

当前三个产品固件已经上报 `firmware_version`。`battery_level` 和 `flash_free` 是可选字段，固件尚未采集时可以不发送。

### 服务端处理

1. 读取 `Authorization` 请求头。
2. 对令牌做 SHA-256 后查询设备。
3. 检查设备是否为 `active`。
4. 更新 IP、固件版本和最后心跳时间。
5. 获取该产品当前的音频版本。

### 服务端响应 ESP32

```json
{
  "ok": true,
  "server_time": "2026-07-31T09:40:00+00:00",
  "device": {
    "id": 7,
    "name": "客厅摇尾熊猫",
    "product_id": "tail-wagging-panda"
  },
  "sync": {
    "revision": "1bb9df75980c4e35",
    "manifest_url": "/api/device/v1/files"
  },
  "heartbeat_interval": 60
}
```

服务端管理页面根据最后心跳判断在线状态。超过 120 秒没有收到 ESP32 心跳，就显示为“离线”。

## 9. 第六步：ESP32 请求音频清单和文件

### 获取音频清单

| 项目 | 内容 |
| --- | --- |
| 发送方 | ESP32 |
| 接收方和响应方 | Audio Hub 服务端 |

```http
GET http://192.168.1.7:5000/api/device/v1/files
Authorization: Bearer zh_...
```

服务端根据设备令牌确定 `product_id`，ESP32 不需要也不能通过查询参数冒充其他产品。

服务端响应：

```json
{
  "product": "tail-wagging-panda",
  "category": "all",
  "revision": "1bb9df75980c4e35",
  "files": [
    {
      "index": 0,
      "name": "熊猫叫声.opus",
      "size": 18324,
      "category": "animal",
      "modified_at": 1785489000
    }
  ]
}
```

### 下载某个音频

ESP32 使用清单中的 `index` 主动请求：

```http
GET http://192.168.1.7:5000/api/device/v1/download/0
Authorization: Bearer zh_...
```

服务端响应为 Opus 文件二进制内容，ESP32 流式写入外接 SPI Flash。

如果使用 `?category=animal` 或 `?category=ambient` 获取分类清单，下载时必须携带相同的分类参数，因为索引是相对于当前清单生成的。

### 当前兼容接口

目前三套固件的 `sync_audio.cc` 仍使用旧接口下载音频：

```text
ESP32 → GET /api/files?product=<product_id> → Audio Hub
ESP32 ← JSON 音频清单 ← Audio Hub

ESP32 → GET /api/download-idx/<index>?product=<product_id> → Audio Hub
ESP32 ← Opus 二进制文件 ← Audio Hub
```

这两个兼容接口暂时不要求设备令牌。设备注册和心跳已经使用新协议，后续把 `sync_audio.cc` 切换到 `/api/device/v1/*` 后，才能安全关闭旧接口。

## 10. 服务端和 ESP32 分别保存什么

| 数据 | Audio Hub 服务端 | ESP32 NVS | 是否可公开 |
| --- | --- | --- | --- |
| `device_id` | 明文保存 | 根据 MAC 生成 | 可以 |
| `product_id` | 明文保存 | 固件编译常量 | 可以 |
| 六位 `activation_code` | 待激活时临时保存 | 保存以便重启后继续显示 | 仅短时间向用户展示 |
| `claim_token` | 只保存 SHA-256 哈希 | `claim_token` 键保存明文 | 不可公开 |
| 正式 `api_token` | 只保存 SHA-256 哈希和前缀 | `api_token` 键保存明文 | 不可公开 |
| 固件版本、IP、最后在线 | 保存 | 固件本身知道版本 | 后台可见 |
| 管理员密码 | 只保存密码哈希 | 不保存 | 不可写入固件 |

## 11. ESP32 当前实现位置

三个产品使用相同的设备注册模块：

```text
newWebServer_Project/tailRedPanda/main/device_registry.cc
newWebServer_Project/crawlPanda/main/device_registry.cc
newWebServer_Project/dinosaur/main/device_registry.cc
```

由各产品的 `main/main.cc` 在 Wi-Fi 连接成功后调用：

```cpp
device_registry_start();
```

模块使用：

- `esp_read_mac(..., ESP_MAC_WIFI_STA)` 生成设备 ID。
- `esp_http_client` 向 Audio Hub 发送 JSON 请求。
- ESP-IDF `cJSON` 解析服务端响应。
- NVS 命名空间 `audio_hub` 保存 `claim_token`、`act_code` 和 `api_token`。
- 独立 FreeRTOS 任务执行注册、3 秒激活轮询和 60 秒心跳，不阻塞舵机与音频任务。

## 12. 状态机

```text
ESP32 启动并读取 NVS
│
├─ NVS 有 api_token
│  └─ ESP32 → 发送心跳 → 服务端响应 → 等待 60 秒 → 重复
│
└─ NVS 没有 api_token
   │
   ├─ NVS 有 claim_token
   │  └─ ESP32 → 查询激活状态
   │     ├─ pending → 等待 3 秒后重试
   │     ├─ active → 保存 api_token → 进入心跳
   │     ├─ 401 → 删除无效 Claim Token → 重新注册
   │     └─ 410 → 激活码过期 → 删除旧凭据 → 重新注册
   │
   └─ NVS 没有 claim_token
      └─ ESP32 → 注册
         ├─ 成功 → 保存 Claim Token、显示六位码、开始轮询
         └─ 网络失败 → 等待 10 秒后重新注册
```

## 13. 常见 HTTP 状态

| HTTP 状态 | 谁会收到 | 含义 | ESP32/管理员应如何处理 |
| --- | --- | --- | --- |
| `200` | ESP32 或浏览器 | 请求成功 | 按响应内容继续 |
| `201` | ESP32 | 首次注册成功或重新签发激活码 | 保存 Claim Token 和激活码 |
| `400` | ESP32 或浏览器 | JSON 字段、设备 ID 或产品 ID 错误 | 检查请求内容 |
| `401` | ESP32 | Claim Token 或 API Token 无效 | 重新注册或由管理员处理令牌 |
| `401` | 管理员浏览器 | 未登录或登录已失效 | 重新登录 |
| `403` | ESP32 | 设备已被管理员停用 | 停止正常业务请求，等待管理员启用 |
| `403` | 管理员浏览器 | CSRF 校验失败 | 刷新管理页面后重试 |
| `409` | ESP32 | 同一设备 ID 使用了不同产品型号 | 检查烧录的产品固件 |
| `410` | ESP32 或浏览器 | 六位激活码已过期 | ESP32 删除旧 Claim Token 后重新注册 |

## 14. 管理员实际操作

1. 在服务器电脑启动 Audio Hub。
2. 管理员打开 `http://192.168.1.7:5000` 并登录。
3. 将目标固件的 `OFFLINE_DEMO` 改为 `0`，配置 Wi-Fi 和服务端 IP。
4. 编译并烧录 ESP32。
5. ESP32 连接 Wi-Fi 后主动注册，在串口显示六位码。
6. 管理员在“设备管理 → 激活码绑定”输入该六位码。
7. ESP32 下一次轮询领取正式令牌并写入 NVS。
8. ESP32 开始每 60 秒发送心跳。
9. 管理员在设备列表看到设备变为在线。

管理员“停用”设备后，ESP32 的正式令牌仍在 NVS 中，但服务端拒绝该令牌。管理员重新启用设备后，同一令牌可以继续使用。

“重新生成令牌”会让旧令牌立即失效。当前固件没有通过网络接收管理员手工生成令牌的接口，因此量产设备优先使用六位激活码流程。

## 15. 安全边界

- 当前设备连接使用局域网 HTTP，适合开发和内网测试。
- 量产部署应改为 HTTPS 固定域名，并为 ESP32 配置服务端证书。
- 管理员密码绝不能写入 ESP32 固件。
- Claim Token 和 API Token 不能输出到普通日志或提交 Git。
- 服务端的 `data/audio_hub.db` 和 `data/session.key` 不能提交到公开仓库。
- 当前旧音频接口仍是公开兼容接口，不应把服务端直接暴露到公网。
