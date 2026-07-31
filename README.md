# Audio Hub 智能玩具管理服务端

Audio Hub 是三个 ESP32 互动玩具共用的设备与音频管理平台。它提供管理员登录、设备激活和在线状态、分产品音频管理、设备鉴权与音频同步接口，同时继续兼容现有固件使用的旧版音频清单接口。

## 支持的产品

| 产品 ID | 产品 | 音频目录 |
| --- | --- | --- |
| `tail-wagging-panda` | 摇尾巴小熊猫 | `audio_files/tail_wagging_panda/` |
| `crawling-panda` | 爬行大熊猫 | `audio_files/crawling_panda/` |
| `dinosaur` | 互动恐龙 | `audio_files/dinosaur/` |

每个产品下分为 `animal`（动物声音）和 `ambient`（环境声音）两个分类。

## 已实现功能

- 管理员登录、会话、CSRF 防护、登录失败限流和修改密码
- 设备列表、搜索、在线状态、停用、删除和重新生成令牌
- 两种添加设备方式：管理员手动添加、设备六位激活码绑定
- 设备 Bearer Token 鉴权、心跳上报和按所属产品获取音频
- `.opus` 多文件/拖拽上传、下载、删除、分类清空和版本摘要
- 中文文件名、路径穿越拦截、文件类型与大小检查、原子写入
- SQLite 持久化，运行数据和源码分离
- 响应式浅色/深色管理界面，不依赖公网 CDN
- 保留 `/api/files` 和 `/api/download-idx`，现有三套固件可继续同步

管理界面的信息层级参考了 [Tabler](https://github.com/tabler/tabler) 和 [Pico CSS](https://github.com/picocss/pico) 的开源后台设计思路；本项目使用自己的离线 HTML/CSS/JavaScript 实现，没有引入第三方 CDN。

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

### 六位激活码（推荐）

这是与“小智”设备控制台相近的绑定流程：

```text
ESP32 首次联网
  → 向服务端登记设备 ID 和产品型号
  → 获得 6 位激活码并在串口/配网页显示
  → 管理员登录 Audio Hub 输入激活码
  → ESP32 轮询到绑定成功，只领取一次设备令牌
  → 令牌写入 NVS，之后用于心跳和音频同步
```

详细字段、状态和固件接入示例见 [设备接入协议](docs/DEVICE_ONBOARDING.md)。

### 管理员手动添加

在“设备管理 → 添加设备”填写设备唯一 ID、产品型号和名称。服务端会显示一次设备令牌，需要将它安全写入对应 ESP32 的 NVS。令牌明文不会保存在数据库中，关闭弹窗后无法再次查看；遗失时只能重新生成。

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
| `GET` | `/api/admin/overview` | 管理员 | 首页汇总 |
| `GET/POST` | `/api/admin/devices` | 管理员 | 查询/手工添加设备 |
| `POST` | `/api/admin/devices/activate` | 管理员 | 输入六位码绑定 |
| `POST` | `/api/device/register` | 公开 | 设备首次登记 |
| `POST` | `/api/device/activate` | Claim Token | 查询绑定并领取令牌 |
| `POST` | `/api/device/v1/check-in` | 设备 | 心跳和运行信息 |
| `GET` | `/api/device/v1/files` | 设备 | 获取本型号音频清单 |
| `GET` | `/api/device/v1/download/<index>` | 设备 | 下载本型号音频 |
| `POST` | `/api/upload` | 管理员 | 上传 Opus 音频 |
| `GET` | `/api/files?product=<id>` | 兼容公开 | 旧固件音频清单 |
| `GET` | `/api/download-idx/<index>?product=<id>` | 兼容公开 | 旧固件按索引下载 |
| `GET` | `/healthz` | 公开 | 服务健康检查 |

兼容接口暂时公开是为了不影响现有固件。三个产品完成设备令牌接入后，建议通过配置关闭旧接口或只允许内网访问。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时目录和临时数据库，不会修改正式音频或 `data/`。

## 运行数据与备份

- `audio_files/`：业务音频，需要定期备份
- `data/audio_hub.db`：管理员、设备和状态
- `data/session.key`：浏览器会话签名密钥

恢复时应同时恢复 `audio_files/` 和 `data/`。不要将数据库、会话密钥或真实设备令牌提交到公开仓库。
