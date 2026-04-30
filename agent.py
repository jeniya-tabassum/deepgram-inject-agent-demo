"""
VoiceAgent: connects to Deepgram Voice Agent over WebSocket and contains
all the business logic the video tutorial walks through.

This module is the source of truth for:
  - When to fire INJECT_QUEUE  (filler during a slow function call)
  - When to fire INJECT_DEFAULT (idle nudge after silence)
  - How InjectionRefused surfaces (when the user races the timer)

The browser (static/inject_demo.js) only does audio I/O + UI rendering.
Everything else lives here.
"""

import asyncio
import json
import logging
from typing import Optional

import websockets

import config
from functions import FUNCTIONS_SPEC, FUNCTION_MAP

logger = logging.getLogger("inject_demo.agent")


def build_settings() -> dict:
    """The Settings payload sent right after the WebSocket opens."""
    return {
        "type": "Settings",
        "audio": {
            "input": {
                "encoding": "linear16",
                "sample_rate": config.INPUT_SAMPLE_RATE,
            },
            "output": {
                "encoding": "linear16",
                "sample_rate": config.OUTPUT_SAMPLE_RATE,
                "container": "none",
            },
        },
        "agent": {
            "listen": {"provider": {"type": "deepgram", "model": config.LISTEN_MODEL}},
            "think": {
                "provider": {"type": config.LLM_PROVIDER, "model": config.LLM_MODEL},
                "prompt": config.PROMPT,
                "functions": FUNCTIONS_SPEC,
            },
            "speak": {"provider": {"type": "deepgram", "model": config.SPEAK_MODEL}},
            "greeting": config.GREETING,
        },
    }


class VoiceAgent:
    """One instance per browser session.

    Owns the WebSocket to Deepgram and pushes UI updates to the browser
    through the socketio instance passed in (`emit("event_name", ...)`).
    """

    def __init__(self, socketio, sid: str):
        self.socketio = socketio
        self.sid = sid
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_running = False

        self.mic_queue: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=200)
        self.idle_timer: Optional[asyncio.Task] = None
        self.agent_speaking = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        # If a nudge already fired in the current idle period, don't
        # fire another one until the user actually says something.
        # Reset to False on UserStartedSpeaking.
        self.nudge_fired_since_user_input = False

    # ---- helpers --------------------------------------------------------

    def emit(self, event: str, data) -> None:
        """Send an event to this browser session over Socket.IO."""
        try:
            self.socketio.emit(event, data, to=self.sid)
        except Exception as e:
            logger.error(f"emit failed ({event}): {e}")

    async def send_json(self, payload: dict) -> None:
        """Send a JSON message to Deepgram and mirror it to the browser."""
        if not self.ws:
            return
        try:
            await self.ws.send(json.dumps(payload))
            self.emit("event_log", {"direction": "->", "type": payload.get("type", "?"), "body": payload})
        except Exception as e:
            logger.error(f"send_json failed: {e}")

    async def send_inject(self, payload: dict, source: str) -> None:
        """Fire an InjectAgentMessage and log it as a tagged event."""
        logger.info(f"-> Inject ({payload['behavior']}) [{source}] {payload['message']!r}")
        self.emit("event_log", {
            "direction": "->",
            "type": f"Inject ({payload['behavior']}) [{source}]",
            "body": payload,
        })
        if self.ws:
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"inject send failed: {e}")

    # ---- USE CASE 1: idle nudge (default) -------------------------------
    #
    # When the agent is idle for IDLE_NUDGE_SECONDS AND nobody speaks,
    # fire INJECT_DEFAULT ("Are you still on the line?"). If the user
    # races the timer and starts talking, the server returns
    # InjectionRefused -- that's the safety net `default` provides.

    def start_idle_timer(self) -> None:
        """Begin the silence countdown; fire INJECT_DEFAULT if it expires.

        Skips if a nudge has already fired in this idle period (i.e. since
        the last UserStartedSpeaking event). This prevents a feedback loop
        where the nudge's own AgentAudioDone restarts the timer and fires
        another nudge, and another, and another...
        """
        self.cancel_idle_timer()
        if not self.is_running:
            return
        if self.nudge_fired_since_user_input:
            return
        self.idle_timer = asyncio.create_task(self._idle_timer_task())

    async def _idle_timer_task(self) -> None:
        try:
            await asyncio.sleep(config.IDLE_NUDGE_SECONDS)
            if not self.is_running:
                return
            logger.info(f"IDLE TIMER fired (silence > {config.IDLE_NUDGE_SECONDS}s)")
            self.emit("event_log", {
                "direction": "*",
                "type": "IDLE TIMER fired",
                "body": f"silence for {config.IDLE_NUDGE_SECONDS}s",
            })
            await self.send_inject(config.INJECT_DEFAULT, "idle-nudge")
            # Mark the flag so we don't fire again until the user speaks.
            self.nudge_fired_since_user_input = True
        except asyncio.CancelledError:
            pass

    def cancel_idle_timer(self) -> None:
        if self.idle_timer:
            self.idle_timer.cancel()
            self.idle_timer = None

    # ---- USE CASE 2: function-call filler (queue) -----------------------
    #
    # FunctionCallRequest arrives while the LLM's pre-function narration
    # ("Looking that up for you.") is mid-flight. We immediately fire
    # INJECT_QUEUE -- queue appends behind the narration so the user
    # hears: preamble -> filler -> answer, no gap.

    async def handle_function_call(self, evt: dict) -> None:
        fns = evt.get("functions") or []
        if fns:
            fn = fns[0]
            fn_id = fn.get("id") or ""
            fn_name = fn.get("name") or ""
            fn_args_raw = fn.get("arguments") or "{}"
        else:
            fn_id = evt.get("function_call_id") or evt.get("id") or ""
            fn_name = evt.get("function_name") or evt.get("name") or ""
            fn_args_raw = evt.get("input") or evt.get("arguments") or "{}"

        logger.info(f"FN_CALL receive {fn_name}({fn_args_raw})")

        # Fire the queue inject NOW. The LLM's pre-function narration is
        # still being spoken, so queue will append behind it.
        await self.send_inject(config.INJECT_QUEUE, "function-filler")

        # Run the slow function (blocks for SLEEP_SECONDS).
        func = FUNCTION_MAP.get(fn_name)
        if not func:
            logger.warning(f"Unknown function: {fn_name}")
            return
        try:
            args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
        except json.JSONDecodeError:
            args = {}

        # Run the (sync) function in a thread so we don't block the loop.
        result = await asyncio.to_thread(func, **args)

        await self.send_json({
            "type": "FunctionCallResponse",
            "id": fn_id,
            "name": fn_name,
            "content": result,
        })

    # ---- main run loop --------------------------------------------------

    async def run(self) -> None:
        """Open the WebSocket, send Settings, and pump messages forever."""
        self.loop = asyncio.get_running_loop()
        self.is_running = True

        try:
            self.ws = await websockets.connect(
                config.VOICE_AGENT_URL,
                additional_headers=[("Authorization", f"Token {config.DEEPGRAM_API_KEY}")],
                max_size=None,
            )
        except Exception as e:
            logger.error(f"failed to connect to Deepgram: {e}")
            self.emit("connection_error", str(e))
            return

        self.emit("event_log", {"direction": "*", "type": "SETUP", "body": "connected, sending Settings"})
        await self.send_json(build_settings())

        try:
            await asyncio.gather(self._sender(), self._receiver())
        except Exception as e:
            logger.error(f"run loop error: {e}")
        finally:
            self.is_running = False
            self.cancel_idle_timer()
            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass
            self.emit("event_log", {"direction": "*", "type": "WS_CLOSE", "body": ""})

    async def _sender(self) -> None:
        """Forward mic audio from the browser to Deepgram."""
        while self.is_running:
            try:
                chunk = await asyncio.wait_for(self.mic_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if self.ws:
                try:
                    await self.ws.send(chunk)
                except Exception:
                    return

    async def _receiver(self) -> None:
        """Receive Deepgram events; forward audio to the browser, dispatch JSON."""
        if not self.ws:
            return
        async for msg in self.ws:
            if not self.is_running:
                break

            if isinstance(msg, bytes):
                # Audio frame from agent -> push to browser to play.
                self.emit("audio_output", msg)
                continue

            try:
                evt = json.loads(msg)
            except json.JSONDecodeError:
                continue

            t = evt.get("type", "?")
            self.emit("event_log", {"direction": "<-", "type": t, "body": evt})

            if t == "ConversationText":
                self.emit("conversation", {
                    "role": evt.get("role", "assistant"),
                    "content": evt.get("content") or evt.get("text") or "",
                })
            elif t == "FunctionCallRequest":
                asyncio.create_task(self.handle_function_call(evt))
            elif t == "AgentStartedSpeaking":
                self.agent_speaking = True
                self.cancel_idle_timer()
            elif t == "AgentAudioDone":
                self.agent_speaking = False
                self.start_idle_timer()
            elif t == "UserStartedSpeaking":
                self.cancel_idle_timer()
                # User is back -- re-arm the nudge for the next idle period.
                self.nudge_fired_since_user_input = False
                self.emit("user_started_speaking", {})

    # ---- called from the Flask thread (Socket.IO handlers) --------------

    def push_mic_chunk(self, chunk: bytes) -> None:
        """Browser pushed a mic frame; queue it for the sender coroutine."""
        if not self.is_running or not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.mic_queue.put(chunk), self.loop)
        except Exception as e:
            logger.error(f"push_mic_chunk failed: {e}")

    def stop(self) -> None:
        self.is_running = False
        self.cancel_idle_timer()
