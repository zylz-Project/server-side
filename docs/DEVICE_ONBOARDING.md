# ESP32 设备接入与绑定协议

本文说明摇尾巴小熊猫、爬行大熊猫和互动恐龙如何接入 Audio Hub。三种产品共用同一套协议，只需使用不同的 `product_id`。

## 1. 设备身份

建议使用 ESP32 出厂 MAC 生成稳定的 `device_id`，例如：

```text
ZYLZ-AABBCCDDEEFF
```

约束：

- 4–64 个字符
- 只使用字母、数字、冒号、点、下划线和连字符
- 每台实体设备永久唯一，不使用随机启动值
- 产品型号写死在对应固件中，不能由用户随意选择

产品 ID：

| 固件 | `product_id` |
| --- | --- |
| 摇尾巴小熊猫 | `tail-wagging-panda` |
| 爬行大熊猫 | `crawling-panda` |
| 互动恐龙 | `dinosaur` |

## 2. 首次注册

设备连接 Wi-Fi 后调用：

```http
POST /api/device/register
Content-Type: application/json
```

```json
{
  "device_id": "ZYLZ-AABBCCDDEEFF",
  "product_id": "tail-wagging-panda",
  "firmware_version": "1.0.0"
}
```

首次登记响应：

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

固件必须立即把 `claim_token` 写入加密 NVS；界面或串口只展示六位 `activation_code`，不得展示 Claim Token。

激活码默认 30 分钟失效。管理员在 Audio Hub 的“绑定激活码”弹窗中输入六位码。

## 3. 查询绑定结果并领取设备令牌

设备按 `poll_after` 间隔轮询：

```http
POST /api/device/activate
Content-Type: application/json
```

```json
{
  "device_id": "ZYLZ-AABBCCDDEEFF",
  "claim_token": "claim_..."
}
```

未绑定：

```json
{"status":"pending","poll_after":3}
```

管理员绑定后，设备只会获得一次正式令牌：

```json
{
  "status": "active",
  "product_id": "tail-wagging-panda",
  "api_token": "zh_..."
}
```

设备应先把 `api_token` 安全写入 NVS，确认写入成功后再删除 Claim Token。此后的请求不要再调用首次注册流程。正式令牌明文不保存在服务端，遗失后需要管理员重新生成并重新配置设备。

## 4. 心跳

设备启动后立即上报一次，之后默认每 60 秒上报：

```http
POST /api/device/v1/check-in
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

`battery_level` 和 `flash_free` 没有采集能力时可以省略。响应包含服务端音频版本：

```json
{
  "ok": true,
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

当 `revision` 与 NVS 中保存的版本不同，启动音频同步；相同时不重复扫描和下载。

管理界面以最后心跳时间判断在线状态，默认超过 120 秒显示离线。

## 5. 鉴权后的音频同步

清单：

```http
GET /api/device/v1/files
Authorization: Bearer zh_...
```

服务端从令牌识别产品，设备不能通过请求参数读取其他型号的音频。

按清单索引下载：

```http
GET /api/device/v1/download/0
Authorization: Bearer zh_...
```

可使用 `?category=animal` 或 `?category=ambient` 只查询/下载一个分类。索引必须来自同一分类的最新清单。

现有固件仍可继续使用：

```text
GET /api/files?product=<product_id>
GET /api/download-idx/<index>?product=<product_id>
```

这两个兼容接口不要求设备令牌，待三套固件完成迁移后再关闭。

## 6. ESP-IDF 端实现建议

每个产品添加一个独立的 `device_registry` 模块，内部使用：

- `esp_efuse_mac_get_default()`：生成稳定设备 ID
- `nvs_flash`：保存 `claim_token`、`api_token`、音频 `revision`
- `esp_http_client`：HTTPS/HTTP JSON 请求
- 独立 FreeRTOS 任务：注册、激活轮询和心跳，避免阻塞动作与音频任务

推荐状态机：

```text
读取 NVS
├─ 有 api_token → check-in → 比较 revision → 必要时同步 → 60 秒后重复
└─ 无 api_token
   ├─ 有 claim_token → 轮询 activate
   └─ 无 claim_token → register → 保存 claim_token → 展示激活码
```

异常处理：

- 网络失败：指数退避，最大等待 60 秒
- HTTP `401`：不要无限注册；先保留日志并等待管理员重新配置
- HTTP `403`：设备已停用，停止业务同步但保持低频状态检查
- Flash/NVS 写入失败：不要删除旧令牌
- 服务地址：量产固件使用 HTTPS 和固定域名，不把管理员密码写入固件

## 7. 管理员操作

1. 登录 Audio Hub。
2. 新设备通电联网，在串口或设备配网页查看六位码。
3. 点击“绑定激活码”，输入六位码。
4. 给设备修改易识别的名称。
5. 在设备列表确认固件版本、IP 和最后心跳。
6. 设备丢失时先“停用”；确认无需保留后再删除。

“重新生成令牌”会立即让旧令牌失效，应只在设备令牌泄露或重新烧录时使用。
