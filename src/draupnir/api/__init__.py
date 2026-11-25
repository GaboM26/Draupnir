"""Localhost web UI and HTTP API (Flask application factory)."""

from flask import Flask

from draupnir.config import Settings
from draupnir.services.valuation import PriceLookup


def create_app(settings: Settings, price: PriceLookup) -> Flask:
    """`price` is the closing-price lookup the valuation pages use (Yahoo in production)."""
    app = Flask(__name__)
    app.config["DRAUPNIR_SETTINGS"] = settings
    app.config["DRAUPNIR_PRICE"] = price

    from draupnir.api.routes import bp

    app.register_blueprint(bp)
    return app
