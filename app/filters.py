from datetime import datetime


def _todatetime(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def register_filters(app):
    app.jinja_env.filters["todatetime"] = _todatetime
