from flask import Flask
from .config import Config
from .extensions import cors
from .filters import register_filters

def create_app(config_class: type[Config] = Config):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_class)

    # Extensions
    cors.init_app(app)

    # Filters
    register_filters(app)

    # Blueprints
    from .routes.pages import bp as pages_bp
    from .routes.api import bp as api
    from .routes.shopify import bp as shopify_pages
    from .routes.shopify_api import bp as shopify_api
    from .routes.printify import bp as printify_pages
    from .routes.printify_api import bp as printify_api
    from .routes.designs import bp as designs_pages
    from .routes.designs_api import bp as designs_api
    from .routes.ai import bp as ai_api
    from .routes.mockups import bp as mockups_pages
    from .routes.mockups_api import bp as mockups_api

    app.register_blueprint(pages_bp)
    app.register_blueprint(api)
    app.register_blueprint(shopify_pages)
    app.register_blueprint(shopify_api, url_prefix="/api")
    app.register_blueprint(printify_pages)
    app.register_blueprint(printify_api, url_prefix="/api")
    app.register_blueprint(designs_pages)
    app.register_blueprint(designs_api, url_prefix="/api")
    app.register_blueprint(ai_api, url_prefix="/api")
    app.register_blueprint(mockups_pages)
    app.register_blueprint(mockups_api, url_prefix="/api")

    return app
