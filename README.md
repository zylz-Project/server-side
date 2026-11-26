# Audio Hub 智能玩具管理服务端

Audio Hub 是三款 ESP32 互动玩具的局域网出厂配置工具。出厂时可配好本地音频、百炼 Key 和设备参数。交付后，设备直接播放本机 Flash 中的音频，使用本机保存的参数，可通过用户自己的网络接入对话及百炼服务。

## 支持的产品

| 产品 ID | 产品 | 音频目录 |
| --- | --- | --- |
| `tail-wagging-panda` | 摇尾巴小熊猫 | `audio_files/tail_wagging_panda/` |
| `crawling-panda` | 爬行大熊猫 | `audio_files/crawling_panda/` |
| `dinosaur` | 互动恐龙 | `audio_files/dinosaur/` |

每个产品下分为 `animal`（动物声音）和 `ambient`（环境声音）两个分类。

## 已实现功能

- 管理员登录、会话、CSRF 防护、登录失败限流和修改密码
- 按热点名查找设备。在同一个设备配置窗口写入离线音频、分配音色 Key 和调音量
- 手动检查时登记设备，默认由服务端启用及签发令牌，支持可选六位激活码绑定
- 设备 Bearer Token 鉴权，手动上报局域网地址并按所属产品检查音频
- 统一分配百炼 Key，每个 Key 最多五台，按设备管理音色
- `.opus` 文件试听、批量拖拽上传、下载、删除、分类清空和版本摘要
- 中文文件名、路径穿越拦截、文件类型/大小/设备端文件名长度检查、原子写入
- SQLite 持久化，运行数据和源码分离
- 响应式浅色/深色管理界面，不依赖公网 CDN
- 保留 `/api/files` 和 `/api/download-idx`，现有三套固件可继续同步

## 快速启动

建议使用 Python 3.10 或更高版本。

```bash
cd audio_serverWeb
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

export AUDIO_HUB_ADMIN_USERNAME=admin
export AUDIO_HUB_ADMIN_PASSWORD='请替换为强密码'
python3 server.py --production
```

浏览器访问 `http://服务器IP:5000`。

如果首次启动时没有设置 `AUDIO_HUB_ADMIN_PASSWORD`，终端会显示一次随机管理员密码。账号创建后保存在 `data/audio_hub.db`；后续修改环境变量不会覆盖已有密码，可在管理界面的“系统设置”中修改。

开发环境也可以直接运行：

```bash
python3 server.py
python3 server.py --host 127.0.0.1 --port 8080
python3 server.py --dir /path/to/audio_files --data-dir /path/to/runtime-data
```

生产环境建议在反向代理后启用 HTTPS，并设置：

```bash
export AUDIO_HUB_SECRET_KEY='长期保存的随机字符串'
export AUDIO_HUB_COOKIE_SECURE=1
```

## 设备添加方式

### 出厂连接与写入

1. 让设备和 Audio Hub 主机连到同一 Wi-Fi。恐龙处于 4G 模式时，在维护期间切到这台 Wi-Fi。
2. 运行 Audio Hub 后，支持自动发现的设备会按热点名出现在工作台。换了局域网地址后自动刷新，点「配置」即可操作。
3. 在同一窗口点击「检查音频」，勾选后写入外置 Flash。默认选中新增和大小有变化的文件，本机独有录音保留。保存音量后，页面会读回设备的音量。
4. 在同一窗口选百炼 Key 后写入，读取和管理这台设备的音色。用「上一台」「下一台」按当前筛选结果继续配置。

工作台默认启用自动接入。启动脚本通过 UDP `39876` 发布带认证的局域网广播，设备验证后登记并上报自己的 Wi-Fi 地址。广播和 HTTP 都使用本机已有的设备授权。面向客户的日常对话和离线互动继续使用设备本地配置，设备只有在收到可验证的工厂信号后才联系 Audio Hub。音频、音色 Key 和音量由操作员在「配置」中保存。

设备名与三款固件的热点一致，例如 `RedPanda-3ADD`、`CrawlPanda-9900` 和 `Dino-1235`。后台根据登记型号和 Wi-Fi MAC 推导名称，已有登记也会自动显示热点名。只包含实验室编号的旧记录沿用登记名称。

在「音频库」先选产品和分类，再试听或上传 `.opus`。批量上传以开始时选择的产品和分类为准，页面保留每个文件的结果。上传完成后，在对应设备的「配置」窗口检查清单并写入。

令牌失效时，设备可在手动检查期间重新注册，复用原设备 ID。后台停用的设备须由管理员重新启用。

### 旧板换刷其他产品固件

保留 NVS 并烧录目标产品固件后，在开发者页面点击「连接并检查」。设备携带
`device_id`、`product_id` 和已有 Bearer Token，服务端确认令牌属于该板后更新
产品型号，后台会相应刷新热点前缀。原登记和 Wi-Fi 配置可沿用。

固件核对上报返回的型号与音频清单中的 `product`，下载接口也核对请求中的
`?product=`。在新型号的清单中手动勾选要写入的音频，同名同大小的文件也可勾选覆盖。

若该板的旧令牌已丢失且后台仍登记为其他型号，
需先在后台修改型号或删除旧记录再重新登记；未鉴权的注册请求仍不能切换已有型号。

三款固件另提供只读 `/api/device`、`/api/status`、`/api/ota/info`，兼容旧整板测试页的
信息轮询；OTA 返回 `supported:false`，并未启用远程升级。若浏览器仍显示旧测试面板，
关闭旧标签页并强制刷新设备首页。Audio Hub 不轮询上述接口，音色管理通过设备开发者接口完成。

### 六位激活码（可选）

需要恢复人工绑定时，启动前设置并重启服务端：

```bash
export AUDIO_HUB_DEVICE_AUTO_ACTIVATE=0
python3 server.py --production
```

手动检查时，新设备会领取六位激活码。管理员在后台绑定后，再从设备点击「连接并检查」。已有启用设备可复用
保存在 NVS 中的令牌。默认值为 `1`，无需额外设置。详细字段见
[设备接入协议](docs/DEVICE_ONBOARDING.md)。

### 管理员手动添加

在「设备」点击「手工添加」，填写产品型号和设备唯一 ID 或 Wi-Fi STA MAC。服务端会显示一次设备令牌，请保存并写入对应 ESP32 的 NVS。

## 百炼 Key 与设备音色

1. 打开「Key 库」或设备配置中的「添加 Key」，保存华北 2（北京）的百炼 API Key。Key 库显示末四位、名额用量和已分配设备，并可检查百炼服务状态。
2. 从设备列表点「配置」，选择 Key 后点击「写入 Key」。要改用另一个 Key 时，先清除原有 Key 再分配。
3. 点击「读取设备音色」，在设备上录音复刻、切换、试听、重命名或删除音色。复刻音色按设备 MAC 区分，共用 Key 的设备各自使用自己的列表。

每个 Key 最多绑定五台。已写入、写入待确认及清除待确认的设备都占用名额。
设备在线确认清除后才释放名额。解绑只清除设备上的 Key，云端已有音色继续保留。
Key 保存在设备本机，用户在其他网络上使用音色时，设备直接访问百炼。

Audio Hub 用本机授权代连设备的局域网接口（TCP `8080`）。登录后台后即可配置，无需在网页输入设备开发者密码。本机授权从运行目录下的私有文件 `device-console.password` 读取，服务端环境变量 `AUDIO_HUB_DEVICE_DEVELOPER_PASSWORD` 可覆盖该配置。不同账号的复刻音色归属各自账号，换账号后可点击「切回原声」再选择新音色。

## 目录结构

```text
audio_serverWeb/
├── server.py                    # 启动入口
├── requirements.txt
├── audio_hub/
│   ├── __init__.py              # Flask 应用工厂与安全响应头
│   ├── catalog.py               # 产品定义和音频文件存储
│   ├── database.py              # SQLite 表结构
│   ├── security.py              # 登录、CSRF、设备令牌
│   ├── routes/                  # 页面、鉴权、音频、设备、系统接口
│   ├── templates/               # 管理页面
│   └── static/                  # 离线 CSS/JavaScript
├── audio_files/                 # 三个产品的 Opus 音频
├── data/                        # 数据库和会话密钥，不提交 Git
├── docs/DEVICE_ONBOARDING.md
└── tests/test_app.py
```

## 主要接口

管理员接口使用登录 Cookie，所有写操作还需 `X-CSRF-Token`。设备接口使用 `Authorization: Bearer <设备令牌>`。

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/api/auth/login` | CSRF | 管理员登录 |
| `GET` | `/api/admin/overview` | 管理员 | 兼容旧后台的汇总接口 |
| `GET/POST` | `/api/admin/devices` | 管理员 | 查询/手工添加设备 |
| `POST` | `/api/admin/devices/connect` | 管理员 | 按局域网 IP 触发设备登记 |
| `GET` | `/api/admin/devices/<id>/factory` | 管理员 | 读取当前音量和已安装音频数 |
| `PUT` | `/api/admin/devices/<id>/factory/volume` | 管理员 | 写入设备音量 |
| `GET/POST` | `/api/admin/devices/<id>/factory/audio` | 管理员 | 查询进度、检查清单和选定音频写入 |
| `GET` | `/api/admin/voice` | 管理员 | 查询脱敏 Key 与设备分配 |
| `POST` | `/api/admin/voice/keys` | 管理员 | 加密保存 Key |
| `PUT/DELETE` | `/api/admin/voice/devices/<id>/key` | 管理员 | 向设备写入或清除 Key |
| `POST` | `/api/admin/voice/devices/<id>/catalog` | 管理员 | 读取或操作该设备的音色 |
| `GET` | `/api/admin/audio?product=<id>&category=<id>` | 管理员 | 一次获取分类清单和产品统计 |
| `GET` | `/api/admin/audio/preview/<filename>` | 管理员 | 按产品和分类试听指定文件 |
| `POST` | `/api/admin/devices/activate` | 管理员 | 输入六位码绑定 |
| `GET` | `/api/device/discovery` | 局域网 | 为设备确认发现的 Audio Hub 服务 |
| `POST` | `/api/device/register` | 公开 | 自动登记并领取令牌（可选手动激活） |
| `POST` | `/api/device/activate` | Claim Token | 查询绑定并领取令牌 |
| `POST` | `/api/device/v1/check-in` | 设备 | 心跳和运行信息 |
| `GET` | `/api/device/v1/files` | 设备 | 获取本型号音频清单 |
| `GET` | `/api/device/v1/download/<index>` | 设备 | 下载本型号音频 |
| `POST` | `/api/upload` | 管理员 | 上传 Opus 音频 |
| `GET` | `/api/files?product=<id>` | 兼容公开 | 旧固件音频清单 |
| `GET` | `/api/download-idx/<index>?product=<id>` | 兼容公开 | 旧固件按索引下载 |
| `GET` | `/healthz` | 公开 | 服务健康检查 |

兼容接口暂时公开是为了不影响现有固件。三个产品完成设备令牌接入后，建议通过配置关闭旧接口或只允许内网访问。

音频文件名按 UTF-8 编码后不能超过 63 字节，这是当前 ESP32 Flash TOC 文件名字段的容量限制。
扩展名统一保存为小写 `.opus`；同一产品的动物声音和环境声音不能使用相同文件名，
因为 ESP32 Flash TOC 以文件名作为唯一标识。

当前固件在出厂维护时手动获取并校验清单。选定文件写入后，设备将清单版本保存到 NVS。

## 服务端性能与下载（2026-10-12）

- 清单、版本号与统计共用同一份目录快照；文件元数据未变化时复用校验和版本计算结果。
  每次请求仍检查文件元数据，上传、删除、外部替换和同大小覆盖都会及时反映。
- 音频页通过 `/api/admin/audio` 一次读取清单与统计；快速切换产品/分类时不会被旧请求覆盖。
  浏览器页面隐藏时暂停设备状态定时刷新。
- 首页一次读取设备及 Key 分配状态，按最近上报排列。注册使用写事务串行处理同一设备的并发登记。
- 所有音频下载接口支持 HTTP `Range`（206）和 `ETag` 重验证（304）；纳秒时间戳参与
  ETag，避免同一秒内替换同大小文件被缓存误判。普通 ESP32 下载请求仍返回完整文件（200）。
  当前固件没有发送 Range 请求，新增续传能力可供浏览器或后续固件使用。

在本机临时目录、每款 32 个文件、500 次调用的测试中，目录统计中位耗时从 0.263 ms
降至 0.044 ms，清单与版本读取从 0.260 ms 降至 0.043 ms。该结果只衡量目录处理，
不代表 Wi-Fi 传输或 Flash 写入速度。原始结果保存在
`../backups/20261012_server_optimization/benchmark_before.json` 和 `benchmark_after.json`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时目录和临时数据库，不会修改正式音频或 `data/`。

## 运行数据与备份

- `audio_files/`：业务音频，需要定期备份
- `data/audio_hub.db`：管理员、设备、状态、加密的百炼 Key 和分配记录
- `data/session.key`：浏览器会话签名密钥
- `data/voice-vault.key`：数据库中百炼 Key 的解密密钥，需要与数据库一起备份

恢复时应同时恢复 `audio_files/` 和 `data/`。数据库、会话密钥、音色解密密钥和真实设备令牌应放在受限备份中。
