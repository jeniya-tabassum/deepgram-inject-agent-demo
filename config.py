"""
All Python-side configuration for the Voice Agent demo.

server.py imports from here so values aren't scattered across files.
The browser fetches a subset of these via /config.
"""

import os

# ----- Deepgram --------------------------------------------------------------

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
VOICE_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"

LISTEN_MODEL = "flux-general-multi"      # Deepgram STT
SPEAK_MODEL = "aura-2-thalia-en"         # Deepgram TTS

# ----- LLM -------------------------------------------------------------------

LLM_PROVIDER = "open_ai"
LLM_MODEL = "gpt-4o"  # mini renders tool args as text; 4o is reliable

# ----- Audio -----------------------------------------------------------------

INPUT_SAMPLE_RATE = 16000   # mic -> Deepgram
OUTPUT_SAMPLE_RATE = 24000  # Deepgram -> speaker

# ----- Server ----------------------------------------------------------------

WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

# ----- Conversation ----------------------------------------------------------

PROMPT = (
    "You are a weather assistant for Dallas, Texas.\n\n"
    "When the user asks about weather, rain, forecast, or "
    "precipitation today, immediately emit a tool_call to "
    "check_rain_today with arguments {\"city\":\"Dallas\"}. The "
    "content of that response must be EMPTY -- do NOT speak any "
    "acknowledgment, do NOT say \"sure\", \"let me check\", \"one "
    "moment\", \"calling the function\", or anything else. The "
    "client application will play filler audio during the wait.\n\n"
    "After the tool result is returned, reply with one short "
    "sentence stating the result verbatim.\n\n"
    "For non-weather questions, chat normally without calling tools."
)

GREETING = (
    "Hello there! I am a demo voice assistant. You can ask me about "
    "the Dallas weather."
)

# Idle nudge: when the agent is idle for this many seconds AND nobody
# is speaking, the client fires INJECT_DEFAULT once. The nudge will
# not fire again until the user has spoken at least once after — to
# avoid an annoying "Are you still there?" loop.
IDLE_NUDGE_SECONDS = 30

# ----- InjectAgentMessage payloads -------------------------------------------
# These are the exact JSON messages the browser sends over the WebSocket.
# They match the canonical examples from the Deepgram docs:
# https://developers.deepgram.com/docs/voice-agent-inject-agent-message
#
# - INJECT_DEFAULT  fires BEFORE the slow function call (plays during silence).
# - INJECT_QUEUE    fires AFTER the slow function call (plays after the answer).

INJECT_DEFAULT = {
    "type": "InjectAgentMessage",
    "behavior": "default",
    "message": "Are you still on the line?",
}

INJECT_QUEUE = {
    "type": "InjectAgentMessage",
    "behavior": "queue",
    "message": "One moment while I pull that up for you.",
}
