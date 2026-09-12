"""Flask application factory wiring Flask + CORS + SocketIO + DB bootstrapping."""

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from api.routes import api_bp
from api.socketio_events import register_socketio_handlers
from config import Config
from database.connection import init_db

socketio = SocketIO(async_mode="threading")


def create_app(test_config: dict | None = None):
    """Build and return ``(app, socketio)`` ready to serve requests."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=Config.SECRET_KEY,
        DATABASE_URI=Config.effective_database_uri(),
        SQL_ECHO=Config.SQL_ECHO,
    )
    if test_config:
        app.config.update(test_config)

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    socketio.init_app(app, cors_allowed_origins="*")

    app.register_blueprint(api_bp)
    register_socketio_handlers(socketio)

    init_db()
    return app, socketio