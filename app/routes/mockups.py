
from flask import Blueprint, request, jsonify, send_from_directory

from .. import Config
from ..extensions import store

bp = Blueprint("mockups_pages", __name__)

@bp.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = Config.MOCKUPS_DIR
    return send_from_directory(dirpath, filename)
