from flask import Blueprint, send_from_directory

from .. import Config

bp = Blueprint("mockups_pages", __name__)

@bp.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = Config.MOCKUPS_DIR
    return send_from_directory(dirpath, filename)


@bp.get('/assets/<path:filename>')
def serve_asset(filename: str):
    return send_from_directory(Config.ASSETS_DIR, filename)
