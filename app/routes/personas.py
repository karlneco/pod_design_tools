from flask import Blueprint, abort, render_template

from ..utils.personas import list_personas, DEFAULT_AGE_SEGMENTS

bp = Blueprint("personas_pages", __name__)


@bp.get("/personas")
def personas_page():
    personas = list_personas(active_only=False)
    return render_template("personas.html", personas=personas, age_segments=DEFAULT_AGE_SEGMENTS)


@bp.get("/personas/<persona_id>/edit")
def persona_edit_page(persona_id: str):
    persona = next((p for p in list_personas(active_only=False) if str(p.get("id")) == persona_id), None)
    if not persona:
        abort(404)
    return render_template("persona_edit.html", persona=persona, age_segments=DEFAULT_AGE_SEGMENTS)
