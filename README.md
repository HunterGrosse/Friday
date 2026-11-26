# Zoey

A personal AI assistant (think Jarvis or FRIDAY), built one layer at a time.

## How an assistant like this actually works

Jarvis and FRIDAY in the movies look like one magic thing. Real ones are a few simple parts stacked together:

```
   ears (speech-to-text)                  mouth (text-to-speech)
            │                                       ▲
            ▼                                       │
 ┌──────────────────────────── Zoey ─────────────────────────────────┐
 │  personality (system prompt) + memory (conversation history)      │
 │                     │                                             │
 │                     ▼                                             │
 │               BRAIN (Claude) ──── decides to use ───► TOOLS       │
 │                     ▲                     (weather, files, lights,│
 │                     └──── tool results ◄── calendar, web, code…)  │
 └───────────────────────────────────────────────────────────────────┘
```

- **Brain**: a large language model (Claude) that you call over an API. It turns text into text.
- **Personality**: a block of instructions (the *system prompt*) sent with every request.
- **Memory**: the model forgets everything between calls. You keep the conversation in a list and send it back every time. Long-term memory means saving facts to disk and loading them back in.
- **Tools**: plain Python functions you describe to the model. The model *asks* you to run one ("call `get_weather` with city=Boston"). Your code runs it and sends back the result. This is how it gets the power to *do* things.
- **Voice**: speech-to-text going in and text-to-speech coming out, wrapped around the same loop.

"Can do anything" just means a lot of tools. Each one you add gives it a new ability.

## Roadmap

| Step | Build | You learn |
|---|---|---|
| **1 ✅** | Terminal chat with a personality and conversation memory | API calls, system prompts, message history, streaming |
| **2 ✅** | Long-term memory: "remember that my sister's birthday is May 3rd" | Saving to and loading from files, giving context to the model |
| **3 ✅** | First tools: time/date, weather, web search | Tool use, the agent loop |
| **4 ✅** | Voice: "Hey Zoey", speech in, her own designed voice out | Audio pipelines, streaming, latency |
| **5 ✅** | Always on (Mac): starts at login, restarts itself, keeps the Mac awake, files conversations away | Background services, config files, reliability |
| 6 | Phone: talk to her from anywhere, same memory | Client/server design, secure remote access |
| 7 | Hands on your computer: read and write files, open apps, run commands (with your approval) | Safety and confirmation gates |
| 8 | Integrations: calendar, email, smart home (Home Assistant), proactive briefings | APIs, OAuth, MCP servers |
| 9 | Interface: a heads-up dashboard (web or desktop) | UI |

## Step 1: talk to it

### 1. Get an API key
Create an account at <https://console.anthropic.com>, add some credit, and create an API key. Normal chatting costs fractions of a cent per message.

### 2. Install and run
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
export ZOEY_USER_NAME="Hunter"         # optional: what Zoey calls you
export ZOEY_HOME_CITY="Tampa"          # optional: default city for weather
python zoey.py
```

Commands inside the chat: `reset` wipes memory, `exit` quits.

### 3. Read `zoey.py`
The comments are part of the lesson. Four numbered sections:

1. **Configuration**: which model, how much effort it spends thinking.
2. **Personality**: the `SYSTEM_PROMPT`. Change it and you change who Zoey is.
3. **Memory**: the `history` list. Each turn the whole list goes back to the model.
4. **The model call**: one streaming request per message, printing words as they arrive.

### 4. Experiments (do these, you'll learn a lot)
1. **Tune the personality.** She's a catty Florida nail tech now. Turn the sass up or down in `SYSTEM_PROMPT`, restart, and see how much a few sentences change her.
2. **Break the memory.** Find `messages=history` and change it to `messages=[history[-1]]` (only the latest message). Tell it your name, then ask what your name is. Then change it back.
3. **Watch it be honest.** Ask her to set an alarm. She has no tool for that, so she should say it isn't wired up yet instead of pretending. (Before Step 3 she said the same about the time; tools fill those gaps.)
4. **Try effort levels.** `ZOEY_EFFORT=high python zoey.py`, then ask a hard logic puzzle. Compare speed and quality with `low`.

## Step 2: long-term memory

Zoey now remembers things between runs. The code is in `memory.py`, and it's
all plain files in a `memory/` folder (git-ignored, so your personal info never
gets committed or pushed).

| You type | What happens |
|---|---|
| `remember that my sister's birthday is May 3rd` | Saved to `memory/facts.json`. Since Step 3 she does this herself with her `save_memory` tool, so any wording works. |
| `memories` | Lists everything she's saved, numbered |
| `forget 2` | Deletes fact #2 |
| `exit` / `reset` / Ctrl+C | She writes a 2-4 sentence recap of the conversation to `memory/journal.jsonl` |

Next time she starts, she loads all the facts, today's date and her last 5
recaps into her system prompt.

### Ideas worth understanding
- **Long-term memory is just text in the prompt.** There's no special brain
  storage. You load files and paste them into the instructions.
- **The system prompt is built once per conversation.** A fact saved
  mid-conversation is already in the chat, so she knows it immediately. It
  moves into the system prompt on the next launch. Changing the system prompt
  in the middle of a conversation makes the API throw away the model's saved
  reasoning and its cache (making it slower and pricier), so the rule is:
  only ever *add to the end* of a conversation, never edit what came before.
- **The model writes its own notes.** The recap is the same model, asked one
  more question at the end. Summarizing old conversations is how assistants
  keep "memory" without resending every chat forever.
- **Why "remember that" and not just "remember"?** So "remember when we..."
  doesn't accidentally get saved. In Step 3 she gets tools, and then she can
  decide by herself what's worth saving.

### Experiments
1. Save three facts, quit, restart, and ask "what do you know about me?"
2. Open `memory/facts.json` in an editor, add a fact by hand, and restart. She
   can't tell the difference, which proves memory is just text.
3. Have a real conversation about some plan, quit, restart, and ask "what were
   we talking about last time?" Then read `memory/journal.jsonl` to see her notes.
4. Change `RECAPS_TO_LOAD` in `memory.py` to 1 and see what she forgets.

## Step 3: tools

Zoey can now *do* things. Just ask naturally:

- "What time is it?" or "What day is Thanksgiving this year?"
- "Do I need an umbrella today?" (set `ZOEY_HOME_CITY` so she knows where you are)
- "What's the latest news on the Bucs?" (she searches the web)
- "My mom's name is Linda and she hates cilantro." (she decides that's worth saving)

You'll see `[checking the weather...]` lines while she works.

### How tools work
Open `tools.py`. Every tool has two halves:

1. **A description** the model reads: a name, what it does, and its inputs.
   The description is how she decides *when* to use it, so it's written like
   instructions to a coworker.
2. **A Python function** that does the work. The model never runs code itself.
   It asks, your program runs the function, and the result goes back.

### The agent loop
Open `run_turn` in `zoey.py`. This loop is the heart of every AI agent, Jarvis included:

```
you: "do I need an umbrella?"
  → model: "call get_weather(city='Tampa')"     stop_reason = tool_use
  → your code runs get_weather, sends back "40% chance of rain..."
  → model: "Take one, babe. 40% and it's Florida."   stop_reason = end_turn
```

She can chain several tools in one answer, or call several at once. Each round
trip gets appended to the conversation so she sees everything she did.

**Web search is different.** It's a *server tool*: Anthropic runs it on their
side, inside the same request, so there's no Python function for it. It costs a
little extra per search, so it's capped at 5 searches per message.

### Experiments
1. **Add your own tool.** Try a `flip_coin` or `roll_dice` tool: write the
   function, add a description to `TOOLS`, and register it in `TOOL_FUNCTIONS`.
   Then ask "flip a coin for who does dishes."
2. **Write a bad description.** Change `get_weather`'s description to
   "Gets data." and ask about rain. Watch her stop using it reliably. Then
   change it back.
3. **Break a tool.** Ask about the weather in a made-up city. The error goes
   back to her, and she handles it like a person would.
4. **Watch her save memories.** Mention something personal in passing, then
   type `memories`.

When you're ready, ask for **Step 4: voice**.

## Step 4: voice

```
mic → "Hey Zoey" → record until you stop → Whisper (speech → text)
    → same brain, tools and memory as text mode
    → her reply streams sentence by sentence → ElevenLabs (text → her voice) → speaker
```

### Setup (about 15 minutes)

**1. Install the voice packages**
```bash
pip install -r requirements-voice.txt
```
Use Python 3.10-3.13 for voice. The speech-recognition package doesn't build
on 3.14+ yet, so on a newer Mac Python run `brew install python@3.13` and make
the venv with `python3.13 -m venv .venv`. On Linux you also need PortAudio: `sudo apt install libportaudio2`. On Mac and
Windows it comes bundled.

**2. Design her voice (ElevenLabs)**
1. Sign up at <https://elevenlabs.io>. The Starter plan is plenty to begin with.
2. Go to **Voices → Add a new voice → Voice Design**, and describe her. For example:
   > A woman in her mid-30s from South Florida with a light Southern-Florida
   > accent. Confident, sassy and quick, with a warm, slightly raspy tone,
   > like she's leaning over a nail station telling you the truth.
3. Generate a few, pick your favorite, save it, and copy its **Voice ID**.
4. Create an API key under **Developers → API Keys**.

**3. Get "Hey Zoey" (Picovoice). Optional, but this is the Jarvis part.**
1. Sign up free at <https://console.picovoice.ai> and copy your **AccessKey**.
2. Under **Porcupine**, type `Hey Zoey`, train it, and download the `.ppn`
   file for your computer (Mac, Windows or Linux).
3. Skip this step and she'll run in push-to-talk mode instead: press Enter, then talk.

**4. Set your keys and run**
```bash
export ELEVENLABS_API_KEY=...
export ELEVENLABS_VOICE_ID=...
export PICOVOICE_ACCESS_KEY=...                       # optional
export PORCUPINE_KEYWORD_PATH=/path/to/Hey-Zoey.ppn # optional
python zoey.py --voice
```
The first launch downloads the Whisper speech model (a few hundred MB).

### Using it
- Say **"Hey Zoey"**, wait a beat, then talk. She records until you pause.
- After she answers she keeps listening for about 5 seconds, so you can follow
  up without the wake word. Go quiet and she goes back to sleep.
- **Ctrl+C** quits. She still writes her recap and says goodbye.
- Text mode (`python zoey.py`) still works exactly as before, and both
  modes share the same memory.

### How it stays fast
- **Streaming all the way through.** Her reply arrives word by word. As soon as
  a full sentence exists it's sent off to ElevenLabs, and audio starts playing
  while she's still writing the next sentence. Look at the `Mouth` class in
  `voice.py`: two background threads form a pipeline (generate audio, then
  play it).
- **She's told she's speaking.** In voice mode her instructions add
  `VOICE_RULES`: short answers, no lists or symbols, numbers as words, and
  allowance for speech-to-text mistakes.
- **Whisper runs on your machine**, so your audio isn't uploaded for
  transcription. Her replies are still sent to ElevenLabs to be spoken.

### Tuning
| Setting | Default | Try |
|---|---|---|
| `ZOEY_WHISPER_MODEL` | `small.en` | `base.en` if transcription is slow; `large-v3-turbo` with an NVIDIA GPU |
| `ELEVENLABS_MODEL` | `eleven_flash_v2_5` (fastest) | `eleven_multilingual_v2` for a richer but slower voice |
| `SILENCE_TO_STOP` in `voice.py` | 0.9 s | Raise it if she cuts you off mid-thought |
| `VAD_AGGRESSIVENESS` in `voice.py` | 2 | 3 in a noisy room |

**Use headphones at first.** She doesn't hear herself while she talks, but
speakers right next to the mic can confuse the follow-up listening.

When you're ready, ask for **Step 5: always on**.

## Step 5: always on (Mac)

Zoey runs as a background service. She starts when you log in, comes back by
herself if she crashes, and you never have to open a terminal to talk to her.

### One-time setup
```bash
cd zoey                              # wherever you cloned this
python3.13 -m venv .venv            # voice needs Python 3.10-3.13 (see Step 4)
.venv/bin/pip install -r requirements-voice.txt
cp .env.example .env                 # then open .env and fill in your keys
.venv/bin/python zoey.py --voice     # test once in Terminal, say "Hey Zoey"
./zoeyctl install                    # make her permanent
```

Testing in Terminal first matters: it makes macOS ask for microphone access,
and it proves your keys work before she disappears into the background.

### Living with her
| Command | What it does |
|---|---|
| `./zoeyctl status` | Is she running? |
| `./zoeyctl logs` | Watch what she hears and says live (Ctrl+C stops watching, not her) |
| `./zoeyctl restart` | Restart her, e.g. after you edit her personality or `.env` |
| `./zoeyctl stop` | She says goodbye and stays off until you `start` her or log in again |
| `./zoeyctl uninstall` | Remove the service. Her memory stays. |

Logs live in `~/Library/Logs/Zoey/zoey.log`.

### What keeps her healthy
- **Settings in `.env`.** Your keys live in one git-ignored file instead of
  being typed each time.
- **Crash recovery.** If she crashes (say the mic disconnects), macOS restarts
  her 10 seconds later. If she exits on purpose because a setting is missing,
  she stays off and says why in the logs, instead of restarting forever.
- **Fresh conversations.** After 15 quiet minutes
  (`ZOEY_SESSION_IDLE_MINUTES`), she writes her recap and the next "Hey Zoey"
  starts a new conversation with today's date and her latest memories. Without
  this, a week of uptime would mean one giant conversation and a stale date.
- **Clean shutdowns.** When you log out, restart, or run `zoeyctl stop`, macOS
  asks her to quit and she still writes her recap.
- **Staying awake.** She stops the Mac from idle-sleeping while she runs
  (`ZOEY_KEEP_AWAKE=0` turns that off). A MacBook still sleeps when you close
  the lid, so for true always-on keep it open or plugged into a display.
- **Mic check.** If macOS blocks the mic, the background gets silence instead
  of an error, so she checks at startup and warns you in the logs.

### If she can't hear you in the background
macOS grants mic access per app. If `./zoeyctl logs` shows the "pure silence"
warning, open **System Settings → Privacy & Security → Microphone** and turn on
**Python**. If Python isn't listed, run `./zoeyctl restart`, watch for a
permission pop-up, and allow it.

When you're ready, ask for **Step 6: your phone**.

## Mac app

Zoey as a real Mac app, with her own name and pink Z icon in the Dock.

```bash
./make_app.sh        # builds ~/Applications/Zoey.app (once)
open -a Zoey         # or Spotlight: "Zoey"
```

| Do this | What happens |
|---|---|
| Click her Dock icon | Talk (if she's off, it turns her on) |
| Right-click her Dock icon | Talk, Turn off / Turn on, Edit personality, Show log, Restart, Quit |
| Watch the badge | 🎙 listening, … thinking, 💬 talking, "off" |

There's also a **Z** in the menu bar with the same menu, if your menu bar has
room (on a MacBook with a notch, extra icons hide behind it).

### Editing her while she runs
The app is a thin launcher: it runs `app.py` from this folder, so there is no
frozen copy to rebuild. (Rebuild only if you move the folder or change Python.)

- **`personality.md`**: save it and her next conversation uses it. (`{name}`
  becomes `ZOEY_USER_NAME`.) She never changes a conversation already in
  progress; that would break its cache, so she files it away and starts fresh.
- **Code or `.env`**: she restarts herself as soon as she's idle and shows a
  notification. If she's mid-conversation, Restart shows "changes waiting".

Turn off and Quit both let her write her recap first. Use the app *or*
`./zoeyctl install`, not both, or two Zoeys will answer you. To start the app
at login: System Settings > General > Login Items > add Zoey.
