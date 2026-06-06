# radar

Basic Flask-SocketIO ping/pong example using eventlet.

```bash
pip install -r requirements.txt
python engine/app.py
```

Connect a Socket.IO client to `http://localhost:5000`, emit `ping`, and the server emits `pong`.
