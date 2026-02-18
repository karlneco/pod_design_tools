from datetime import datetime, timezone
import os
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from ..utils.personas import (
    DEFAULT_AGE_SEGMENTS,
    PERSONAS_COLLECTION,
    list_personas,
    normalize_generation_orientation,
    normalize_generation_resolution,
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


def _persona_aspect_ratio(orientation: str) -> str:
    return "3:4" if normalize_generation_orientation(orientation) == "portrait" else "1:1"


def _persona_size_label(resolution: int) -> str:
    r = normalize_generation_resolution(resolution)
    if r >= 4096:
        return "4K"
    if r >= 2048:
        return "2K"
    return "1K"


def _persona_model_for_size(image_size: str) -> str:
    size = str(image_size or "").strip().upper()
    if size in {"2K", "4K"}:
        return os.getenv("GEMINI_PERSONA_HD_MODEL", "gemini-3-pro-image-preview").strip()
    return os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image").strip()


def _history_append(
    existing: dict,
    *,
    filename: str,
    prompt: str = "",
    source: str = "upload",
    reference_filename: str = "",
) -> list[dict]:
    history = list(existing.get("render_history") or [])
    history.append({
        "filename": filename,
        "prompt": str(prompt or "").strip(),
        "reference_filename": str(reference_filename or "").strip(),
        "source": str(source or "upload").strip() or "upload",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return history


def _resolve_or_bootstrap_persona(persona_id: str):
    existing = store.get(PERSONAS_COLLECTION, persona_id)
    if existing:
        return existing
    fallback = None
    for p in sorted(personas_dir().iterdir()):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if _slugify(p.stem) == persona_id:
            fallback = {
                "id": persona_id,
                "label": p.stem.replace("_", " "),
                "image_filename": p.name,
                "age_options": DEFAULT_AGE_SEGMENTS,
                "notes": "",
                "gender": "unspecified",
                "archetype": "",
                "location": "",
                "occupation": "",
                "generation_prompt": "",
                "generation_orientation": "square",
                "generation_resolution": 2048,
                "render_history": [],
                "source": "upload",
                "active": True,
            }
            break
    if not fallback:
        return None
    return {
        "id": persona_id,
        "label": str(fallback.get("label") or persona_id),
        "image_filename": str(fallback.get("image_filename") or ""),
        "age_segments": fallback.get("age_options") or DEFAULT_AGE_SEGMENTS,
        "notes": str(fallback.get("notes") or ""),
        "gender": str(fallback.get("gender") or "unspecified"),
        "archetype": str(fallback.get("archetype") or ""),
        "location": str(fallback.get("location") or ""),
        "occupation": str(fallback.get("occupation") or ""),
        "generation_prompt": str(fallback.get("generation_prompt") or ""),
        "generation_orientation": normalize_generation_orientation(fallback.get("generation_orientation")),
        "generation_resolution": normalize_generation_resolution(fallback.get("generation_resolution")),
        "render_history": list(fallback.get("render_history") or []),
        "source": str(fallback.get("source") or "upload"),
        "active": bool(fallback.get("active", True)),
    }


@bp.get("/personas")
def api_personas_list():
    return jsonify({"personas": list_personas(active_only=False), "age_segments": DEFAULT_AGE_SEGMENTS})


@bp.post("/personas")
def api_personas_create():
    label = (request.form.get("label") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    gender = (request.form.get("gender") or "unspecified").strip()
    archetype = (request.form.get("archetype") or "").strip()
    location = (request.form.get("location") or "").strip()
    occupation = (request.form.get("occupation") or "").strip()
    generation_orientation = normalize_generation_orientation(request.form.get("generation_orientation"))
    generation_resolution = normalize_generation_resolution(request.form.get("generation_resolution"))
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
        gender=gender,
        archetype=archetype,
        location=location,
        occupation=occupation,
        generation_orientation=generation_orientation,
        generation_resolution=generation_resolution,
        render_history=[{
            "filename": out_name,
            "prompt": "",
            "reference_filename": "",
            "source": "upload",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
        source="upload",
        active=True,
    )
    return jsonify({"ok": True, "persona": doc, "image_url": f"/assets/personas/{out_name}"})


@bp.post("/personas/generate")
def api_personas_generate():
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    notes = (body.get("notes") or "").strip()
    gender = (body.get("gender") or "unspecified").strip()
    archetype = (body.get("archetype") or "").strip()
    location = (body.get("location") or "").strip()
    occupation = (body.get("occupation") or "").strip()
    generation_orientation = normalize_generation_orientation(body.get("generation_orientation"))
    generation_resolution = normalize_generation_resolution(body.get("generation_resolution"))
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
        f"Compose for a {generation_orientation} frame. "
        f"Persona details: {brief}"
    )
    aspect_ratio = _persona_aspect_ratio(generation_orientation)
    image_size = _persona_size_label(generation_resolution)
    persona_model = _persona_model_for_size(image_size)
    try:
        generated = generate_lifestyle_images(
            prompt,
            num_images=1,
            reference_local_paths=[],
            reference_urls=[],
            image_aspect_ratio=aspect_ratio,
            image_size=image_size,
            model_override=persona_model,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    img = generated[0]
    ext = ".png"

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
        gender=gender,
        archetype=archetype,
        location=location,
        occupation=occupation,
        generation_prompt=prompt,
        generation_orientation=generation_orientation,
        generation_resolution=generation_resolution,
        render_history=[{
            "filename": out_name,
            "prompt": prompt,
            "reference_filename": "",
            "source": "generated",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
        source="generated",
        active=True,
    )
    return jsonify({"ok": True, "persona": doc, "image_url": f"/assets/personas/{out_name}"})


@bp.post("/personas/<persona_id>")
def api_personas_update(persona_id: str):
    existing = _resolve_or_bootstrap_persona(persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404

    if request.is_json:
        body = request.get_json(silent=True) or {}
        label = (body.get("label") if "label" in body else existing.get("label")) or ""
        notes = (body.get("notes") if "notes" in body else existing.get("notes")) or ""
        generation_prompt = (body.get("generation_prompt") if "generation_prompt" in body else existing.get("generation_prompt")) or ""
        generation_orientation = normalize_generation_orientation(
            body.get("generation_orientation") if "generation_orientation" in body else existing.get("generation_orientation")
        )
        generation_resolution = normalize_generation_resolution(
            body.get("generation_resolution") if "generation_resolution" in body else existing.get("generation_resolution")
        )
        gender = (body.get("gender") if "gender" in body else existing.get("gender")) or "unspecified"
        archetype = (body.get("archetype") if "archetype" in body else existing.get("archetype")) or ""
        location = (body.get("location") if "location" in body else existing.get("location")) or ""
        occupation = (body.get("occupation") if "occupation" in body else existing.get("occupation")) or ""
        active = bool(body.get("active")) if "active" in body else bool(existing.get("active", True))
        age_segments = parse_age_segments(body.get("age_segments") if "age_segments" in body else existing.get("age_segments"))
        image_filename = existing.get("image_filename")
    else:
        label = (request.form.get("label") or existing.get("label") or "").strip()
        notes = (request.form.get("notes") or existing.get("notes") or "").strip()
        generation_prompt = (request.form.get("generation_prompt") or existing.get("generation_prompt") or "").strip()
        generation_orientation = normalize_generation_orientation(
            request.form.get("generation_orientation") or existing.get("generation_orientation")
        )
        generation_resolution = normalize_generation_resolution(
            request.form.get("generation_resolution") or existing.get("generation_resolution")
        )
        gender = (request.form.get("gender") or existing.get("gender") or "unspecified").strip()
        archetype = (request.form.get("archetype") or existing.get("archetype") or "").strip()
        location = (request.form.get("location") or existing.get("location") or "").strip()
        occupation = (request.form.get("occupation") or existing.get("occupation") or "").strip()
        active = str(request.form.get("active", str(existing.get("active", True)))).lower() in ("1", "true", "yes", "on")
        age_segments = parse_age_segments(request.form.get("age_segments") or existing.get("age_segments"))
        image_filename = existing.get("image_filename")
        files = request.files.getlist("photo")
        photo = next((f for f in files if f and str(f.filename or "").strip()), None)
        if photo:
            fname = secure_filename(photo.filename or "")
            ext = _allowed_ext(fname)
            if not ext:
                return jsonify({"error": "Unsupported image extension"}), 400
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            out_name = f"{_slugify(label or persona_id)}_{stamp}{ext}"
            out_path = personas_dir() / out_name
            photo.save(out_path)
            image_filename = out_name
            existing["render_history"] = _history_append(
                existing,
                filename=out_name,
                prompt="",
                source="upload",
                reference_filename="",
            )

    doc = upsert_persona(
        persona_id=persona_id,
        label=label,
        image_filename=image_filename,
        age_segments=age_segments,
        notes=notes,
        gender=gender,
        archetype=archetype,
        location=location,
        occupation=occupation,
        generation_prompt=generation_prompt,
        generation_orientation=generation_orientation,
        generation_resolution=generation_resolution,
        render_history=existing.get("render_history"),
        source=existing.get("source") or "upload",
        active=active,
    )
    return jsonify({"ok": True, "persona": doc})


@bp.post("/personas/<persona_id>/regenerate")
def api_personas_regenerate(persona_id: str):
    existing = _resolve_or_bootstrap_persona(persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404

    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or existing.get("generation_prompt") or "").strip()
    generation_orientation = normalize_generation_orientation(
        body.get("generation_orientation") or existing.get("generation_orientation")
    )
    generation_resolution = normalize_generation_resolution(
        body.get("generation_resolution") or existing.get("generation_resolution")
    )
    if not prompt:
        prompt = (
            "Neutral studio portrait photo, full body, clean white seamless background, soft diffused lighting, "
            "natural skin texture, realistic proportions, non-celebrity, no brand logos, no text, "
            "wearing plain white t-shirt and blue jeans, front-facing and relaxed pose. "
            f"Compose for a {generation_orientation} frame. "
            f"Persona details: {existing.get('label') or persona_id}"
        )
    aspect_ratio = _persona_aspect_ratio(generation_orientation)
    image_size = _persona_size_label(generation_resolution)
    persona_model = _persona_model_for_size(image_size)

    reference_filename = str(body.get("reference_filename") or "").strip()
    reference_local_paths: list[str] = []
    if reference_filename:
        ref_path = personas_dir() / reference_filename
        if not ref_path.exists() or not ref_path.is_file():
            return jsonify({"error": "reference image file not found"}), 404
        reference_local_paths.append(str(ref_path))

    try:
        generated = generate_lifestyle_images(
            prompt,
            num_images=1,
            reference_local_paths=reference_local_paths,
            reference_urls=[],
            image_aspect_ratio=aspect_ratio,
            image_size=image_size,
            model_override=persona_model,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    img = generated[0]
    ext = ".png"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_name = f"{_slugify(existing.get('label') or persona_id)}_{stamp}{ext}"
    out_path = personas_dir() / out_name
    out_path.write_bytes(img["bytes"])

    history = _history_append(
        existing,
        filename=out_name,
        prompt=prompt,
        source="generated",
        reference_filename=reference_filename,
    )
    set_as_main = bool(body.get("set_as_main"))
    doc = upsert_persona(
        persona_id=persona_id,
        label=str(existing.get("label") or persona_id),
        image_filename=out_name if set_as_main else str(existing.get("image_filename") or out_name),
        age_segments=existing.get("age_segments"),
        notes=str(existing.get("notes") or ""),
        gender=str(existing.get("gender") or "unspecified"),
        archetype=str(existing.get("archetype") or ""),
        location=str(existing.get("location") or ""),
        occupation=str(existing.get("occupation") or ""),
        generation_prompt=prompt,
        generation_orientation=generation_orientation,
        generation_resolution=generation_resolution,
        render_history=history,
        source=existing.get("source") or "generated",
        active=bool(existing.get("active", True)),
    )
    return jsonify({"ok": True, "persona": doc, "image_url": f"/assets/personas/{out_name}", "filename": out_name})


@bp.post("/personas/<persona_id>/main-image")
def api_personas_set_main_image(persona_id: str):
    existing = _resolve_or_bootstrap_persona(persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404
    body = request.get_json(silent=True) or {}
    filename = str(body.get("filename") or "").strip()
    if not filename:
        return jsonify({"error": "filename is required"}), 400
    target = personas_dir() / filename
    if not target.exists() or not target.is_file():
        return jsonify({"error": "image file not found"}), 404
    doc = upsert_persona(
        persona_id=persona_id,
        label=str(existing.get("label") or persona_id),
        image_filename=filename,
        age_segments=existing.get("age_segments"),
        notes=str(existing.get("notes") or ""),
        gender=str(existing.get("gender") or "unspecified"),
        archetype=str(existing.get("archetype") or ""),
        location=str(existing.get("location") or ""),
        occupation=str(existing.get("occupation") or ""),
        generation_prompt=str(existing.get("generation_prompt") or ""),
        generation_orientation=normalize_generation_orientation(existing.get("generation_orientation")),
        generation_resolution=normalize_generation_resolution(existing.get("generation_resolution")),
        render_history=list(existing.get("render_history") or []),
        source=existing.get("source") or "upload",
        active=bool(existing.get("active", True)),
    )
    return jsonify({"ok": True, "persona": doc})


@bp.post("/personas/<persona_id>/images/delete")
def api_personas_delete_image(persona_id: str):
    existing = _resolve_or_bootstrap_persona(persona_id)
    if not existing:
        return jsonify({"error": "persona not found"}), 404

    body = request.get_json(silent=True) or {}
    filename = str(body.get("filename") or "").strip()
    if not filename:
        return jsonify({"error": "filename is required"}), 400

    history = list(existing.get("render_history") or [])
    remaining_history = [h for h in history if str((h or {}).get("filename") or "") != filename]
    if len(remaining_history) == len(history):
        return jsonify({"error": "image not found in render history"}), 404
    if not remaining_history:
        return jsonify({"error": "Cannot delete the last image. Delete persona instead."}), 400

    current_main = str(existing.get("image_filename") or "").strip()
    new_main = current_main
    if current_main == filename:
        new_main = str((remaining_history[-1] or {}).get("filename") or "").strip()
        if not new_main:
            return jsonify({"error": "Could not choose replacement main image"}), 400

    doc = upsert_persona(
        persona_id=persona_id,
        label=str(existing.get("label") or persona_id),
        image_filename=new_main,
        age_segments=existing.get("age_segments"),
        notes=str(existing.get("notes") or ""),
        gender=str(existing.get("gender") or "unspecified"),
        archetype=str(existing.get("archetype") or ""),
        location=str(existing.get("location") or ""),
        occupation=str(existing.get("occupation") or ""),
        generation_prompt=str(existing.get("generation_prompt") or ""),
        generation_orientation=normalize_generation_orientation(existing.get("generation_orientation")),
        generation_resolution=normalize_generation_resolution(existing.get("generation_resolution")),
        render_history=remaining_history,
        source=existing.get("source") or "upload",
        active=bool(existing.get("active", True)),
    )

    # Remove file from disk if it's no longer used by this persona record.
    still_used = {str(doc.get("image_filename") or "")}
    for h in (doc.get("render_history") or []):
        if isinstance(h, dict):
            hfn = str(h.get("filename") or "").strip()
            if hfn:
                still_used.add(hfn)
    if filename not in still_used:
        p = personas_dir() / filename
        if p.exists() and p.is_file():
            p.unlink(missing_ok=True)

    return jsonify({"ok": True, "persona": doc, "deleted_filename": filename})


@bp.delete("/personas/<persona_id>")
def api_personas_delete(persona_id: str):
    existing = _resolve_or_bootstrap_persona(persona_id)
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
