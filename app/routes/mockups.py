from flask import Blueprint, send_from_directory, render_template

from .. import Config

bp = Blueprint("mockups_pages", __name__)

@bp.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = Config.MOCKUPS_DIR
    return send_from_directory(dirpath, filename)


@bp.get('/assets/<path:filename>')
def serve_asset(filename: str):
    return send_from_directory(Config.ASSETS_DIR, filename)


@bp.get("/designs/<slug>/mockup-editor")
def mockup_editor(slug: str):
    product_id = None
    if slug.startswith("shopify-"):
        product_id = slug.split("shopify-", 1)[1]
    return render_template("mockup_editor.html", slug=slug, product_id=product_id)


@bp.get("/designs/shopify-<product_id>/mockups/<path:filename>")
def serve_product_mockup(product_id: str, filename: str):
    dirpath = Config.PRODUCT_MOCKUPS_DIR / f"shopify-{product_id}" / "mockups"
    return send_from_directory(dirpath, filename)
