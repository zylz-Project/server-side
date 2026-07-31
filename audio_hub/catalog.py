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
        "emoji": "🦊",
        "color": "#f47a3c",
        "description": "头部与双轴尾巴互动玩具",
        "directory": "tail_wagging_panda",
    },
    "crawling-panda": {
        "id": "crawling-panda",
        "name": "爬行大熊猫",
        "short_name": "爬行熊猫",
        "emoji": "🐼",
        "color": "#13a66a",
        "description": "头部与双臂爬行动作玩具",
        "directory": "crawling_panda",
    },
    "dinosaur": {
        "id": "dinosaur",
        "name": "互动恐龙",
        "short_name": "恐龙",
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
        if len(name.encode("utf-8")) > 63:
            raise AudioStoreError("文件名过长，UTF-8 编码后不能超过 63 字节")
        return name

    @staticmethod
    def _sort_key(path: Path) -> tuple[str, str]:
        return path.name.casefold(), path.name

    def manifest(self, product_id: str, category_id: str | None = None) -> list[dict]:
        if product_id not in PRODUCTS:
            raise AudioStoreError("无效的产品 ID")
        if category_id and category_id not in CATEGORIES:
            raise AudioStoreError("无效的音频分类")

        categories = [category_id] if category_id else list(CATEGORIES)
        files: list[dict] = []
        for current_category in categories:
            directory = self.directory(product_id, current_category)
            paths = sorted(
                (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".opus"),
                key=self._sort_key,
            )
            for path in paths:
                stat = path.stat()
                files.append(
                    {
                        "index": len(files),
                        "name": path.name,
                        "size": stat.st_size,
                        "category": current_category,
                        "modified_at": int(stat.st_mtime),
                        "modified_at_ns": stat.st_mtime_ns,
                    }
                )
        return files

    def revision(self, product_id: str) -> str:
        digest = hashlib.sha256()
        for item in self.manifest(product_id):
            digest.update(
                f"{item['category']}\0{item['name']}\0{item['size']}\0{item['modified_at_ns']}\n".encode(
                    "utf-8"
                )
            )
        return digest.hexdigest()[:16]

    def summary(self, product_id: str) -> dict:
        totals = {}
        for category_id, category in CATEGORIES.items():
            files = self.manifest(product_id, category_id)
            totals[category_id] = {
                "id": category_id,
                "name": category["name"],
                "emoji": category["emoji"],
                "count": len(files),
                "size": sum(item["size"] for item in files),
            }
        return {
            "product": product_id,
            "totals": totals,
            "total": sum(item["count"] for item in totals.values()),
            "total_size": sum(item["size"] for item in totals.values()),
            "revision": self.revision(product_id),
        }

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
                os.replace(temp_path, directory / name)
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
        return {"name": name, "size": size, "category": category_id}

    def clear(self, product_id: str, category_id: str) -> int:
        directory = self.directory(product_id, category_id)
        count = 0
        with self._lock:
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() == ".opus":
                    path.unlink()
                    count += 1
        return count
