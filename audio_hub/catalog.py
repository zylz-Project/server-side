"""Product catalog and safe filesystem-backed audio storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import BinaryIO


PRODUCTS = {
    "tail-wagging-panda": {
        "id": "tail-wagging-panda",
        "name": "摇尾巴小熊猫",
        "short_name": "摇尾熊猫",
        "ap_prefix": "RedPanda",
        "console_build": "wzh-tailredpanda",
        "emoji": "🦊",
        "color": "#f47a3c",
        "description": "头部与双轴尾巴互动玩具",
        "directory": "tail_wagging_panda",
    },
    "crawling-panda": {
        "id": "crawling-panda",
        "name": "爬行大熊猫",
        "short_name": "爬行熊猫",
        "ap_prefix": "CrawlPanda",
        "console_build": "wzh-crawlpanda",
        "emoji": "🐼",
        "color": "#13a66a",
        "description": "头部与双臂爬行动作玩具",
        "directory": "crawling_panda",
    },
    "dinosaur": {
        "id": "dinosaur",
        "name": "互动恐龙",
        "short_name": "恐龙",
        "ap_prefix": "Dino",
        "console_build": "wzh-dinosaur",
        "emoji": "🦖",
        "color": "#3b82f6",
        "description": "五关节动作互动玩具",
        "directory": "dinosaur",
    },
}

CATEGORIES = {
    "animal": {"id": "animal", "name": "动物声音", "emoji": "🐾"},
    "ambient": {"id": "ambient", "name": "环境声音", "emoji": "🌿"},
}


class AudioStoreError(ValueError):
    pass


class AudioStore:
    def __init__(self, root: Path, max_file_size: int):
        self.root = root.resolve()
        self.max_file_size = max_file_size
        self._lock = threading.RLock()
        self._snapshots: dict[str, tuple[tuple, list[dict], str]] = {}

    def ensure_directories(self) -> None:
        for product_id in PRODUCTS:
            for category_id in CATEGORIES:
                self.directory(product_id, category_id).mkdir(parents=True, exist_ok=True)

    def directory(self, product_id: str, category_id: str) -> Path:
        product = PRODUCTS.get(product_id)
        if not product:
            raise AudioStoreError("无效的产品 ID")
        if category_id not in CATEGORIES:
            raise AudioStoreError("无效的音频分类")
        return self.root / product["directory"] / category_id

    @staticmethod
    def safe_filename(filename: str) -> str:
        name = unicodedata.normalize("NFC", (filename or "").strip())
        if not name or name in {".", ".."}:
            raise AudioStoreError("文件名不能为空")
        if "/" in name or "\\" in name or '"' in name or any(ord(ch) < 32 for ch in name):
            raise AudioStoreError("文件名包含非法字符")
        if not name.lower().endswith(".opus"):
            raise AudioStoreError("只支持 .opus 文件")
        name = f"{name[:-5]}.opus"
        if len(name.encode("utf-8")) > 63:
            raise AudioStoreError("文件名过长，UTF-8 编码后不能超过 63 字节")
        return name

    def _snapshot(self, product_id: str) -> tuple[list[dict], str]:
        if product_id not in PRODUCTS:
            raise AudioStoreError("无效的产品 ID")
        # Read metadata on every request so uploads and external replacements are
        # visible immediately. Cache sorting/validation/hashing, not freshness.
        with self._lock:
            entries = []
            for category in CATEGORIES:
                with os.scandir(self.directory(product_id, category)) as iterator:
                    for entry in iterator:
                        if not entry.name.lower().endswith(".opus"):
                            continue
                        try:
                            if not entry.is_file():
                                continue
                            stat = entry.stat()
                        except FileNotFoundError:
                            continue  # An external tool removed it during the scan.
                        entries.append((category, entry.name, stat.st_size,
                                        stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
            category_order = {name: index for index, name in enumerate(CATEGORIES)}
            entries.sort(key=lambda item: (category_order[item[0]], item[1].casefold(), item[1]))
            signature = tuple(entries)
            cached = self._snapshots.get(product_id)
            if cached and cached[0] == signature:
                return cached[1], cached[2]

            files = []
            names = set()
            digest = hashlib.sha256()
            for category, name, size, mtime_ns, _ctime_ns, _inode in entries:
                normalized = self.safe_filename(name)
                if normalized != name:
                    raise AudioStoreError(f"音频扩展名必须为小写 .opus：{name}")
                if name in names:
                    raise AudioStoreError(f"同一产品的不同分类不能使用相同文件名：{name}")
                names.add(name)
                files.append({
                    "index": len(files), "name": name, "size": size,
                    "category": category, "modified_at": mtime_ns // 1_000_000_000,
                    "modified_at_ns": mtime_ns,
                })
                # Preserve the existing revision format to avoid a redundant sync
                # when deploying this server update.
                digest.update(f"{category}\0{name}\0{size}\0{mtime_ns}\n".encode("utf-8"))
            revision = digest.hexdigest()[:16]
            self._snapshots[product_id] = (signature, files, revision)
            return files, revision

    @staticmethod
    def _summary(product_id: str, files: list[dict], revision: str) -> dict:
        totals = {
            key: {"id": key, "name": category["name"], "emoji": category["emoji"],
                  "count": 0, "size": 0}
            for key, category in CATEGORIES.items()
        }
        for item in files:
            totals[item["category"]]["count"] += 1
            totals[item["category"]]["size"] += item["size"]
        return {
            "product": product_id, "totals": totals, "total": len(files),
            "total_size": sum(item["size"] for item in files), "revision": revision,
        }

    def listing(self, product_id: str, category_id: str | None = None,
                include_summary: bool = False) -> dict:
        if category_id and category_id not in CATEGORIES:
            raise AudioStoreError("无效的音频分类")
        files, revision = self._snapshot(product_id)
        selected = [dict(item) for item in files
                    if not category_id or item["category"] == category_id]
        for index, item in enumerate(selected):
            item["index"] = index
        payload = {"product": product_id, "category": category_id or "all",
                   "revision": revision, "files": selected}
        if include_summary:
            payload["summary"] = self._summary(product_id, files, revision)
        return payload

    def manifest(self, product_id: str, category_id: str | None = None) -> list[dict]:
        return self.listing(product_id, category_id)["files"]

    def revision(self, product_id: str) -> str:
        return self._snapshot(product_id)[1]

    def summary(self, product_id: str) -> dict:
        files, revision = self._snapshot(product_id)
        return self._summary(product_id, files, revision)

    def path_by_name(self, product_id: str, filename: str, category_id: str | None = None) -> Path:
        name = self.safe_filename(filename)
        categories = [category_id] if category_id else list(CATEGORIES)
        for current_category in categories:
            if not current_category:
                continue
            candidate = self.directory(product_id, current_category) / name
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(name)

    def path_by_index(self, product_id: str, index: int, category_id: str | None = None) -> Path:
        files = self.manifest(product_id, category_id)
        if index < 0 or index >= len(files):
            raise FileNotFoundError(str(index))
        item = files[index]
        return self.directory(product_id, item["category"]) / item["name"]

    def save(self, product_id: str, category_id: str, filename: str, stream: BinaryIO) -> dict:
        name = self.safe_filename(filename)
        directory = self.directory(product_id, category_id)
        directory.mkdir(parents=True, exist_ok=True)

        temp_path: Path | None = None
        total = 0
        try:
            with tempfile.NamedTemporaryFile(prefix=".upload-", dir=directory, delete=False) as temp:
                temp_path = Path(temp.name)
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > self.max_file_size:
                        raise AudioStoreError("单个音频不能超过 32 MB")
                    temp.write(chunk)
                temp.flush()
                os.fsync(temp.fileno())
            if total == 0:
                raise AudioStoreError("不能上传空文件")
            with self._lock:
                for other_category in CATEGORIES:
                    if other_category == category_id:
                        continue
                    if (self.directory(product_id, other_category) / name).is_file():
                        raise AudioStoreError(
                            f"另一个分类已存在同名文件：{name}"
                        )
                os.replace(temp_path, directory / name)
                self._snapshots.pop(product_id, None)
            temp_path = None
            return {"name": name, "size": total, "category": category_id}
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def delete(self, product_id: str, category_id: str, filename: str) -> dict:
        name = self.safe_filename(filename)
        path = self.directory(product_id, category_id) / name
        with self._lock:
            if not path.is_file():
                raise FileNotFoundError(name)
            size = path.stat().st_size
            path.unlink()
            self._snapshots.pop(product_id, None)
        return {"name": name, "size": size, "category": category_id}

    def clear(self, product_id: str, category_id: str) -> int:
        directory = self.directory(product_id, category_id)
        count = 0
        with self._lock:
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() == ".opus":
                    path.unlink()
                    count += 1
            self._snapshots.pop(product_id, None)
        return count
