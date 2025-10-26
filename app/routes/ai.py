from flask import Blueprint, request, jsonify

from ..extensions import store

bp = Blueprint("ai_api", __name__)
