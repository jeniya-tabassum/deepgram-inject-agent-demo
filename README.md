# InjectAgentMessage Demo

A Python-centric demo of Deepgram Voice Agent's [`InjectAgentMessage`](https://developers.deepgram.com/docs/voice-agent-inject-agent-message) API. Walks through the two `behavior` values — `default` and `queue` — using their canonical use cases, and surfaces the `InjectionRefused` event when the timing's wrong.

The browser is a thin audio I/O layer. All inject decisions live in `agent.py`.

## Architecture

```
Browser ──Socket.IO──▶ Flask (server.py) ──WebSocket──▶ Deepgram Voice Agent
          (audio_data)              │                              │
                                    │     VoiceAgent (agent.py)    │
                                    │     ─ idle timer (default)   │
                                    │     ─ function call (queue)  │
                                    │     ─ inject senders         │
                                    │                              │
          ◀──Socket.IO──── Flask ◀──┴──────────────────────────────┘
          (audio_output, event_log, conversation)
```

## What it shows

| Scenario | Behavior | What you see |
|---|---|---|
| **Filler during a slow function call** | `queue` | After you ask about the weather, *"One moment while I pull that up for you"* plays during the 5-second function wait, then the rain answer follows. |
| **Idle nudge after silence** | `default` | After 5 seconds of silence, *"Are you still on the line?"* plays once. |
| **Refused inject** | `default` (during user speech) | If you start talking right as the idle timer fires, the server returns `InjectionRefused (USER_MID_TURN)`. The nudge gets dropped. |

Every event is captured in a timestamped log under `logs/`.

## Quick start

```bash
# 1. Clone + create venv
git clone <repo-url>
cd Voice_Agent
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your Deepgram API key
export DEEPGRAM_API_KEY=your_key_here

# 4. Run
python server.py
```

Open <http://localhost:8000>, allow microphone access, click **Start Voice Agent**.

## File structure

```
Voice_Agent/
├── agent.py             VoiceAgent class — connects to Deepgram, fires injects, runs the idle timer
├── config.py            All configuration: prompt, models, the two InjectAgentMessage payloads
├── functions.py         The slow check_rain_today tool (5s sleep, canned answer)
├── server.py            Flask + Socket.IO bridge between browser and VoiceAgent
├── inject_demo.html     Sidebar + Conversation + Event Timeline (~70 lines)
├── static/
│   ├── style.css        UI styling
│   └── inject_demo.js   Browser audio I/O + Socket.IO event display
├── requirements.txt
├── VIDEO_SCRIPT.md      Tutorial script for recording a walkthrough video
└── logs/                Auto-created; one timestamped session log per server run
```

## How it works

### The two InjectAgentMessage payloads

Both live as Python dicts in `config.py`:

```python
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

IDLE_NUDGE_SECONDS = 5
```

Match the canonical examples from the [docs](https://developers.deepgram.com/docs/voice-agent-inject-agent-message).

### Where each behavior fires

The `VoiceAgent` class in `agent.py` has two fire points:

**1. Idle nudge (`default`)** — after `AgentAudioDone`, an asyncio timer waits 5 seconds; if no one speaks, it fires `INJECT_DEFAULT`. The timer is cancelled by any `AgentStartedSpeaking` or `UserStartedSpeaking` event. Fires at most **once** per idle period — the next nudge can't fire until the user actually speaks.

**2. Function-call filler (`queue`)** — when `FunctionCallRequest` arrives, the handler immediately fires `INJECT_QUEUE` and runs the slow function on a thread. Because the prompt forbids the LLM from emitting a preamble, nothing is queued when the inject lands → server speaks it immediately, filling the silent wait.

### How `InjectionRefused` shows up

If the user starts talking right as the idle timer fires, the inject lands while the user is mid-turn. The server returns `InjectionRefused (USER_MID_TURN)` and the nudge is dropped. That's the safety net `default` provides — the demo shows it via timing: just say something at the 5-second mark.

## Configuration

All knobs in `config.py`:

| Setting | Default | Notes |
|---|---|---|
| `LISTEN_MODEL` | `flux-general-multi` | Deepgram STT model |
| `SPEAK_MODEL` | `aura-2-thalia-en` | Deepgram TTS voice |
| `LLM_PROVIDER` / `LLM_MODEL` | `open_ai` / `gpt-4o` | Model that decides when to call the tool. `gpt-4o-mini` is unreliable with tool calls — sometimes renders args as text. Stick with `gpt-4o` unless you've verified mini works for your account. |
| `INPUT_SAMPLE_RATE` | `16000` | Mic → Deepgram |
| `OUTPUT_SAMPLE_RATE` | `24000` | Deepgram → speaker |
| `IDLE_NUDGE_SECONDS` | `5` | Silence threshold before the default nudge fires |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8000` | Flask server |

The system prompt and greeting strings live in `config.py` too.

## Logs

Each `python server.py` invocation writes a timestamped log file:

```
logs/session_2026-04-30_01-12-34.log
```

Both server and browser events are merged into one file with millisecond timestamps. Useful for debugging timing issues, inspecting the exact JSON sent over the WebSocket, or verifying which behavior fired when.

`tail -f logs/session_*.log` for a real-time view.

## Troubleshooting

**The LLM never calls the function (no `FunctionCallRequest` in the timeline).**
You're probably on `gpt-4o-mini`. Switch to `gpt-4o` in `config.py` — mini sometimes serializes the tool arguments into the assistant's content field instead of emitting a structured `tool_call`. The pattern in the timeline is an `[assistant]` ConversationText containing `{"city":"Dallas"}` as plain text.

**The queue inject plays AFTER the rain answer instead of during the wait.**
The LLM emitted a preamble before the tool call. Per docs, queue waits for the in-flight think response — including any post-function answer. Check the prompt in `config.py`: it should explicitly forbid spoken acknowledgments. If you see an `[assistant]` ConversationText between `[user]` and `<- FunctionCallRequest`, that's the issue.

**The idle nudge fires repeatedly in a loop.**
Confirm `nudge_fired_since_user_input` is set in `_idle_timer_task` after the inject. Without it, the nudge's own `AgentAudioDone` restarts the timer and you get *"Are you still on the line?"* every 5 seconds.

**WebSocket closes mid-session with `code=1005`.**
Deepgram timed the connection out due to no audio input. The browser's `inject_demo.js` sends silent frames during agent speech to keep the connection alive — make sure `socket.emit('audio_data', ...)` is firing every audio tick (check the browser console).

**`InjectionRefused` doesn't appear when you talk during the timer.**
Timing is tight. The 5-second timer fires almost exactly at 5s after `AgentAudioDone`. To race it, count silently to ~4.5s then start a word. If you talk too early the timer is cancelled before it fires; too late and the nudge plays first.

## Recording a tutorial video

See [`VIDEO_SCRIPT.md`](VIDEO_SCRIPT.md) — a 7–8 minute walkthrough script with timestamps, lines to say, what to highlight on screen, and a pre-flight checklist.

## License

MIT
