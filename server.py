#!/usr/bin/env python3
"""
Flask + Socket.IO server for the InjectAgentMessage demo.

Architecture (everything browser-side is just thin I/O):

  Browser ──Socket.IO──▶ this server ──WebSocket──▶ Deepgram Voice Agent
            (audio_data)                            (audio + JSON events)
            (start/stop)                                       │
                              ◀──Socket.IO──── this server ◀───┘
                              (audio_output, event_log, conversation)

All business logic — idle timer, function-call handling, inject decisions —
lives in agent.py. This file is only routing.

Run:
    export DEEPGRAM_API_KEY=...
    pip install flask flask-socketio websockets eventlet
    python server.py
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_socketio import SocketIO

import config
from agent import VoiceAgent

# ----- logging ---------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"session_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logger = logging.getLogger("inject_demo.server")

# ----- Flask + Socket.IO -----------------------------------------------------

app = Flask(__name__, static_folder="static", static_url_path="/static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# One VoiceAgent per connected browser session.
agents: dict[str, VoiceAgent] = {}


@app.route("/")
def index():
    return send_from_directory(".", "inject_demo.html")


@app.route("/config")
def get_config():
    """Public config the browser needs to render the page."""
    if not config.DEEPGRAM_API_KEY:
        return jsonify({"error": "DEEPGRAM_API_KEY env var not set"}), 500

    return jsonify({
        "inputSampleRate": config.INPUT_SAMPLE_RATE,
        "outputSampleRate": config.OUTPUT_SAMPLE_RATE,
        "idleNudgeSeconds": config.IDLE_NUDGE_SECONDS,
        "injectDefault": config.INJECT_DEFAULT,
        "injectQueue": config.INJECT_QUEUE,
    })


# ----- Socket.IO handlers ----------------------------------------------------

@socketio.on("connect")
def on_connect():
    from flask import request
    logger.info(f"[browser] connected sid={request.sid}")


@socketio.on("disconnect")
def on_disconnect():
    from flask import request
    sid = request.sid
    logger.info(f"[browser] disconnected sid={sid}")
    agent = agents.pop(sid, None)
    if agent:
        agent.stop()


@socketio.on("start_voice_agent")
def on_start_voice_agent():
    """Spin up a VoiceAgent for this browser session in a background thread."""
    from flask import request
    sid = request.sid
    if sid in agents and agents[sid].is_running:
        return
    if not config.DEEPGRAM_API_KEY:
        socketio.emit("connection_error", "DEEPGRAM_API_KEY not set", to=sid)
        return

    agent = VoiceAgent(socketio, sid)
    agents[sid] = agent

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(agent.run())
        except Exception as e:
            logger.error(f"agent {sid} crashed: {e}")
        finally:
            loop.close()
            agents.pop(sid, None)

    threading.Thread(target=_run, daemon=True, name=f"agent-{sid}").start()
    logger.info(f"[browser] start_voice_agent sid={sid}")


@socketio.on("stop_voice_agent")
def on_stop_voice_agent():
    from flask import request
    sid = request.sid
    agent = agents.pop(sid, None)
    if agent:
        agent.stop()
        logger.info(f"[browser] stop_voice_agent sid={sid}")


@socketio.on("audio_data")
def on_audio_data(data):
    """Browser pushed a mic chunk; forward it to the agent's WebSocket."""
    from flask import request
    sid = request.sid
    agent = agents.get(sid)
    if not agent or not agent.is_running:
        return
    chunk = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    agent.push_mic_chunk(chunk)


# ----- entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    if not config.DEEPGRAM_API_KEY:
        print("ERROR: DEEPGRAM_API_KEY env var is required.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("  InjectAgentMessage Demo (Python-centric)")
    logger.info("=" * 60)
    logger.info(f"  Open  http://localhost:{config.WEB_PORT}")
    logger.info(f"  Logs  {LOG_FILE}")
    logger.info(f"  Stop  Ctrl+C")
    logger.info("=" * 60)

    socketio.run(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
