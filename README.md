# Audio Hub 多产品音频管理服务端

基于 Python 3 和 Flask 的 ESP32 智能玩具音频管理服务。服务端为三款产品分别管理 Opus 音频，提供浏览器管理页面、设备同步 API，以及设备 Flash 状态代理。

## 支持的产品

| 产品 ID | 产品 | 存储目录 |
| --- | --- | --- |
| `tail-wagging-panda` | 摇尾巴小熊猫 | `audio_files/tail_wagging_panda/` |
| `crawling-panda` | 爬行大熊猫 | `audio_files/crawling_panda/` |
| `dinosaur` | 恐龙 | `audio_files/dinosaur/` |

每个产品下包含两种音频分类：

| 分类 ID | 说明 |
| --- | --- |
| `animal` | 动物叫声、进食声、脚步声等 |
| `ambient` | 虫鸣、鸟叫、雨声、流水等环境声音 |

## 目录结构

```text
server-side/
├── server.py
├── requirements.txt
├── README.md
└── audio_files/
    ├── tail_wagging_panda/
    │   ├── animal/
    │   └── ambient/
    ├── crawling_panda/
    │   ├── animal/
    │   └── ambient/
    └── dinosaur/
        ├── animal/
        └── ambient/
```

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

默认监听 `0.0.0.0:5000`。浏览器访问：

```text
http://<服务端电脑的局域网 IP>:5000
```

可选参数：

```bash
python3 server.py --host 0.0.0.0 --port 8080
python3 server.py --dir /path/to/audio_files
```

## Web 管理功能

- 在三款产品之间切换；
- 按 `animal` 和 `ambient` 分类管理音频；
- 选择或拖拽上传多个 `.opus` 文件；
- 查看文件数量和占用空间；
- 删除文件或清空指定分类；
- 通过服务端代理查看或擦除 ESP32 外接 Flash。

服务端仅接受 `.opus` 文件，单文件最大 32 MB。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | Web 管理页面 |
| `GET` | `/api/summary?product=<id>` | 获取指定产品的分类统计 |
| `GET` | `/api/files?product=<id>[&category=<category>]` | 获取文件清单；不传分类时合并全部分类 |
| `GET` | `/api/download/<filename>?product=<id>[&category=<category>]` | 按文件名下载 |
| `GET` | `/api/download-idx/<index>?product=<id>[&category=<category>]` | 按清单索引下载，供 ESP32 同步使用 |
| `POST` | `/api/upload` | 上传文件，表单包含 `file`、`product`、`category` |
| `DELETE` | `/api/delete/<filename>?product=<id>&category=<category>` | 删除文件 |
| `POST` | `/api/clear?product=<id>&category=<category>` | 清空指定分类 |
| `GET` | `/api/proxy-flash?ip=<device-ip>` | 代理读取 ESP32 Flash 状态 |
| `GET` | `/api/proxy-flash-erase?ip=<device-ip>` | 代理请求 ESP32 擦除 Flash |

上传、删除和清空操作必须同时提供合法的产品 ID 和分类 ID。

## curl 示例

```bash
# 查看恐龙的全部音频
curl "http://localhost:5000/api/files?product=dinosaur"

# 查看恐龙环境音
curl "http://localhost:5000/api/files?product=dinosaur&category=ambient"

# 上传动物音效
curl -F "file=@sound.opus" \
     -F "product=dinosaur" \
     -F "category=animal" \
     http://localhost:5000/api/upload

# 按索引下载
curl "http://localhost:5000/api/download-idx/0?product=dinosaur" -o sound.opus

# 删除文件
curl -X DELETE \
  "http://localhost:5000/api/delete/sound.opus?product=dinosaur&category=animal"
```

## ESP32 同步流程

```text
ESP32 启动并连接 Wi-Fi
  → GET /api/files?product=<产品 ID>
  → 比较服务端清单与外接 Flash TOC
  → GET /api/download-idx/<索引>?product=<产品 ID>
  → 流式写入外接 Flash
  → 更新 TOC v2 文件索引
```

`/api/download-idx/` 的索引顺序与 `/api/files` 返回顺序保持一致。设备端不要重新排序清单，否则可能下载到错误文件。

## 安全说明

当前服务没有登录鉴权，上传、删除、清空和设备 Flash 代理接口均面向局域网使用。不要将服务端直接暴露到公网；部署时应通过防火墙、反向代理认证或 VPN 限制访问范围。
