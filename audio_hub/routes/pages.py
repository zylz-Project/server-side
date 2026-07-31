from flask import Blueprint, redirect, render_template, session, url_for

from ..security import csrf_token


pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/login")
def login_page():
    if session.get("admin_id"):
        return redirect(url_for("pages.dashboard"))
    return render_template("login.html", csrf_token=csrf_token())


@pages_bp.get("/")
def dashboard():
    if not session.get("admin_id"):
        return redirect(url_for("pages.login_page"))
    return render_template(
        "index.html",
        csrf_token=csrf_token(),
        username=session.get("username", "admin"),
    )
