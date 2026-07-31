from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, send_file

from ..catalog import CATEGORIES, PRODUCTS, AudioStoreError
from ..security import api_error, csrf_protect, login_required


audio_bp = Blueprint("audio", __name__, url_prefix="/api")


def _store():
    return current_app.extensions["audio_store"]


def _product_id() -> str:
    return (request.args.get("product") or request.form.get("product") or "").strip()


def _category_id(required: bool = False) -> str | None:
    value = (request.args.get("category") or request.form.get("category") or "").strip()
    if required and not value:
        raise AudioStoreError("缺少音频分类")
    return value or None


def _nocache(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@audio_bp.get("/products")
def products():
    return jsonify(products=list(PRODUCTS.values()), categories=list(CATEGORIES.values()))


@audio_bp.get("/summary")
@login_required
def summary():
    try:
        return _nocache(jsonify(_store().summary(_product_id())))
    except AudioStoreError as exc:
        return api_error(str(exc))


@audio_bp.get("/files")
def files():
    """Compatibility manifest used by existing ESP32 firmware."""
    product_id = _product_id()
    category_id = _category_id()
    try:
        items = _store().manifest(product_id, category_id)
        return _nocache(
            jsonify(
                product=product_id,
                category=category_id or "all",
                revision=_store().revision(product_id),
                files=items,
            )
        )
    except AudioStoreError as exc:
        return api_error(str(exc))


@audio_bp.get("/download/<path:filename>")
def download(filename: str):
    try:
        path = _store().path_by_name(_product_id(), filename, _category_id())
        return send_file(path, mimetype="application/octet-stream", download_name=path.name, conditional=False)
    except AudioStoreError as exc:
        return api_error(str(exc))
    except FileNotFoundError:
        return api_error("音频文件不存在", 404)


@audio_bp.get("/download-idx/<int:index>")
def download_index(index: int):
    try:
        path = _store().path_by_index(_product_id(), index, _category_id())
        return send_file(path, mimetype="application/octet-stream", download_name=path.name, conditional=False)
    except AudioStoreError as exc:
        return api_error(str(exc))
    except FileNotFoundError:
        return api_error("音频索引不存在", 404)


@audio_bp.post("/upload")
@login_required
@csrf_protect
def upload():
    product_id = _product_id()
    try:
        category_id = _category_id(required=True)
        uploads = request.files.getlist("file")
        if not uploads or not any(item.filename for item in uploads):
            return api_error("请选择要上传的 Opus 文件")
        saved = []
        for upload_file in uploads:
            if upload_file.filename:
                saved.append(
                    _store().save(product_id, category_id, upload_file.filename, upload_file.stream)
                )
        current_app.logger.info("上传音频 [%s/%s]: %d 个", product_id, category_id, len(saved))
        return jsonify(ok=True, files=saved, revision=_store().revision(product_id)), 201
    except AudioStoreError as exc:
        return api_error(str(exc))


@audio_bp.delete("/delete/<path:filename>")
@login_required
@csrf_protect
def delete(filename: str):
    product_id = _product_id()
    try:
        category_id = _category_id(required=True)
        deleted = _store().delete(product_id, category_id, filename)
        current_app.logger.info("删除音频 [%s/%s]: %s", product_id, category_id, filename)
        return jsonify(ok=True, file=deleted, revision=_store().revision(product_id))
    except AudioStoreError as exc:
        return api_error(str(exc))
    except FileNotFoundError:
        return api_error("音频文件不存在", 404)


@audio_bp.post("/clear")
@login_required
@csrf_protect
def clear():
    product_id = _product_id()
    try:
        category_id = _category_id(required=True)
        count = _store().clear(product_id, category_id)
        current_app.logger.warning("清空音频 [%s/%s]: %d 个", product_id, category_id, count)
        return jsonify(ok=True, deleted=count, revision=_store().revision(product_id))
    except AudioStoreError as exc:
        return api_error(str(exc))
