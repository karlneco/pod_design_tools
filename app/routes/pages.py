from flask import Blueprint, jsonify, render_template

bp = Blueprint("pages", __name__)

@bp.get("/")
def index():
    return render_template("index.html")

@bp.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200
