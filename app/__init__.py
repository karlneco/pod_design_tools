from flask import Flask
from .config import Config
from .extensions import cors, store, printify_client, shopify_client
from .filters import register_filters

def create_app(config_class: type[Config] = Config):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_class)

    # Extensions
    cors.init_app(app)
    store.init_app(app, app.config["DATA_DIR"])
    printify_client.init_app(app)   # loads PRINTIFY_* from env
    shopify_client.init_app(app)    # loads SHOPIFY_* from env

    # Filters
    register_filters(app)

    # Blueprints
    from .routes.pages import bp as pages_bp
    from .routes.shopify import bp as shopify_bp
    from .routes.printify import bp as printify_bp
    from .routes.designs import bp as designs_bp
    from .routes.ai import bp as ai_bp
    from .routes.mockups import bp as mockups_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(shopify_bp, url_prefix="/api/shopify")
    app.register_blueprint(printify_bp)
    app.register_blueprint(designs_bp, url_prefix="/api/designs")
    app.register_blueprint(ai_bp, url_prefix="/api")
    app.register_blueprint(mockups_bp)

    return app
