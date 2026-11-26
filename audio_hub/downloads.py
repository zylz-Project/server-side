"""Stream audio with range requests and precise cache validators."""

from pathlib import Path

from flask import send_file


def send_audio(path: Path, mimetype: str = "application/octet-stream"):
    stat = path.stat()
    etag = f"{stat.st_ino:x}-{stat.st_size:x}-{stat.st_mtime_ns:x}-{stat.st_ctime_ns:x}"
    response = send_file(
        path, mimetype=mimetype, download_name=path.name,
        conditional=True, etag=etag, max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response
