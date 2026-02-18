from flask import Blueprint, render_template

from ..utils.personas import list_personas, DEFAULT_AGE_SEGMENTS

bp = Blueprint("personas_pages", __name__)


@bp.get("/personas")
def personas_page():
    personas = list_personas(active_only=False)
    return render_template("personas.html", personas=personas, age_segments=DEFAULT_AGE_SEGMENTS)

