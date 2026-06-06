import eventlet

eventlet.monkey_patch()

from flask import Flask
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")


@socketio.on("ping")
def handle_ping():
    emit("pong")


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
