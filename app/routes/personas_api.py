from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from ..utils.personas import (
    DEFAULT_AGE_SEGMENTS,
    PERSONAS_COLLECTION,
    list_personas,
    parse_age_segments,
    personas_dir,
    upsert_persona,
)
from ..extensions import store
from ..services.gemini_svc import generate_lifestyle_images

bp = Blueprint("personas_api", __name__)


def _slugify(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    out = "-".join(part for part in out.split("-") if part)
    return out or "persona"


def _allowed_ext(filename: str) -> str | None:
    ext = Path(filename or "").suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return ext
    return None


@bp.get("/personas")
def api_personas_list():
    return jsonify({"personas": list_personas(active_only=False), "age_segments": DEFAULT_AGE_SEGMENTS})


@bp.post("/personas")
def api_personas_create():
    label = (request.form.get("label") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    age_segments = parse_age_segments(request.form.get("age_segments"))
    if not label:
        return jsonify({"error": "label is required"}), 400

    files = request.files.getlist("photo")
    if not files:
        return jsonify({"error": "photo file is required"}), 400

    photo = files[0]
    filename = secure_filename(photo.filename or "")
    ext = _allowed_ext(filename)
    if not ext:
        return jsonify({"error": "Unsupported image extension"}), 400

    pid = _slugify(label)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_name = f"{pid}_{stamp}{ext}"
    out_path = personas_dir() / out_name
    photo.save(out_path)

    doc = upsert_persona(
        persona_id=pid,
        label=label,
        image_filename=out_name,
        age_segments=age_segments,
        notes=notes,
        source="upload",
        active=True,
    )
    return jsonify({"ok": True, "persona": doc, "image_url": f"/assets/personas/{out_name}"})


@bp.post("/personas/generate")
def api_personas_generate():
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    notes = (body.get("notes") or "").strip()
    brief = (body.get("brief") or "").strip()
    age_segments = parse_age_segments(body.get("age_segments"))
    if not label:
        return jsonify({"error": "label is required"}), 400
    if not brief:
        return jsonify({"error": "brief is required"}), 400

    prompt = (
        "Neutral studio portrait photo, full body, clean white seamless background, soft diffused lighting, "
        "natural skin texture, realistic proportions, non-celebrity, no brand logos, no text, "
        "wearing plain white t-shirt and blue jeans, front-facing and relaxed pose. "
        f"Persona details: {brief}"
    )
    try:
        generated = generate_lifestyle_images(prompt, num_images=1, reference_local_paths=[], reference_urls=[])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    img = generated[0]
    ext = ".png"
    mime = str(img.get("mime_type") or "").lower()
    if "jpeg" in mime or "jpg" in mime:
        ext = ".jpg"
    elif "webp" in mime:
        ext = ".webp"

    pid = _slugify(label)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_name = f"{pid}_{stamp}{ext}"
    out_path = personas_dir() / out_name
    out_path.write_bytes(img["bytes"])

    doc = upsert_persona(
        persona_id=pid,
        label=label,
        image_filename=out_name,
        age_segments=age_segments,
        notes=notes or brief,
        source="generated",
        active=True,
    )
    return jsonify({"ok": True, "persona": doc, "image_url": f"/assets/personas/{out_name}"})


@bp.post("/personas/<persona_id>")
def api_personas_update(persona_id: str):
    existing = store.get(PERSONAS_COLLECTION, persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404

    if request.is_json:
        body = request.get_json(silent=True) or {}
        label = (body.get("label") if "label" in body else existing.get("label")) or ""
        notes = (body.get("notes") if "notes" in body else existing.get("notes")) or ""
        active = bool(body.get("active")) if "active" in body else bool(existing.get("active", True))
        age_segments = parse_age_segments(body.get("age_segments") if "age_segments" in body else existing.get("age_segments"))
        image_filename = existing.get("image_filename")
    else:
        label = (request.form.get("label") or existing.get("label") or "").strip()
        notes = (request.form.get("notes") or existing.get("notes") or "").strip()
        active = str(request.form.get("active", str(existing.get("active", True)))).lower() in ("1", "true", "yes", "on")
        age_segments = parse_age_segments(request.form.get("age_segments") or existing.get("age_segments"))
        image_filename = existing.get("image_filename")
        files = request.files.getlist("photo")
        if files:
            photo = files[0]
            fname = secure_filename(photo.filename or "")
            ext = _allowed_ext(fname)
            if not ext:
                return jsonify({"error": "Unsupported image extension"}), 400
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            out_name = f"{_slugify(label or persona_id)}_{stamp}{ext}"
            out_path = personas_dir() / out_name
            photo.save(out_path)
            image_filename = out_name

    doc = upsert_persona(
        persona_id=persona_id,
        label=label,
        image_filename=image_filename,
        age_segments=age_segments,
        notes=notes,
        source=existing.get("source") or "upload",
        active=active,
    )
    return jsonify({"ok": True, "persona": doc})


@bp.delete("/personas/<persona_id>")
def api_personas_delete(persona_id: str):
    existing = store.get(PERSONAS_COLLECTION, persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404
    delete_image = str(request.args.get("delete_image", "false")).lower() in ("1", "true", "yes", "on")
    if delete_image:
        fn = str(existing.get("image_filename") or "").strip()
        if fn:
            p = personas_dir() / fn
            if p.exists():
                p.unlink(missing_ok=True)
    store.delete(PERSONAS_COLLECTION, persona_id)
    return jsonify({"ok": True})

