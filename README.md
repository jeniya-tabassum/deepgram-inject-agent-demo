# InjectAgentMessage Demo

A Python demo of Deepgram Voice Agent's [`InjectAgentMessage`](https://developers.deepgram.com/docs/voice-agent-inject-agent-message) API. Walks through the two `behavior` values — `default` and `queue` — using their canonical use cases, and surfaces the `InjectionRefused` event when the timing's wrong.


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

