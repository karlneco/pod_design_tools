import os

from app import create_app
from app.config import DevConfig

app = create_app(DevConfig)

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5003"))
    debug = os.getenv("DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
