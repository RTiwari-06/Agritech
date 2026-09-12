"""Framework entrypoint: Flask + SocketIO server.

Run:
    python main.py

Then launch the Streamlit frontend in a second terminal:
    streamlit run frontend/app.py
"""

from api.app import create_app
from config import Config

app, socketio = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
    )