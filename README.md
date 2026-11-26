# FRIDAY

A personal AI assistant, built one layer at a time.

## How an assistant like this actually works

Movie FRIDAY looks like one magic thing. Real ones are a few simple parts stacked together:

```
   ears (speech-to-text)                  mouth (text-to-speech)
            │                                       ▲
            ▼                                       │
 ┌──────────────────────────── FRIDAY ─────────────────────────────┐
 │  personality (system prompt) + memory (conversation history)    │
 │                     │                                            │
 │                     ▼                                            │
 │               BRAIN (Claude) ──── decides to use ───► TOOLS      │
 │                     ▲                     (weather, files, lights,│
 │                     └──── tool results ◄── calendar, web, code…)  │
 └──────────────────────────────────────────────────────────────────┘
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
| 3 | First tools: time/date, weather, web search | Tool use, the agent loop |
| 4 | Hands on your computer: read and write files, open apps, run commands (with your approval) | Safety and confirmation gates |
| 5 | Voice: wake word ("Hey FRIDAY"), speech in, spoken replies | Audio pipelines, latency |
| 6 | Integrations: calendar, email, smart home (Home Assistant), phone notifications | APIs, OAuth, MCP servers |
| 7 | Always on: runs in the background, proactive briefings, scheduled tasks | Services, scheduling |
| 8 | Interface: a heads-up dashboard (web or desktop) | UI |

## Step 1: talk to it

### 1. Get an API key
Create an account at <https://console.anthropic.com>, add some credit, and create an API key. Normal chatting costs fractions of a cent per message.

### 2. Install and run
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
export FRIDAY_USER_NAME="Hunter"         # optional: what FRIDAY calls you
python friday.py
```

Commands inside the chat: `reset` wipes memory, `exit` quits.

### 3. Read `friday.py`
It's about 100 lines, and the comments are part of the lesson. Four sections:

1. **Configuration**: which model, how much effort it spends thinking.
2. **Personality**: the `SYSTEM_PROMPT`. Change it and you change who FRIDAY is.
3. **Memory**: the `history` list. Each turn the whole list goes back to the model.
4. **The model call**: one streaming request per message, printing words as they arrive.

### 4. Experiments (do these, you'll learn a lot)
1. **Tune the personality.** She's a catty Florida nail tech now. Turn the sass up or down in `SYSTEM_PROMPT`, restart, and see how much a few sentences change her.
2. **Break the memory.** Find `messages=history` and change it to `messages=[history[-1]]` (only the latest message). Tell it your name, then ask what your name is. Then change it back.
3. **Watch it be honest.** Ask "what time is it?" It should say it can't check yet. That's the gap Step 3 fills with tools.
4. **Try effort levels.** `FRIDAY_EFFORT=high python friday.py`, then ask a hard logic puzzle. Compare speed and quality with `low`.

## Step 2: long-term memory

FRIDAY now remembers things between runs. The code is in `memory.py`, and it's
all plain files in a `memory/` folder (git-ignored, so your personal info never
gets committed or pushed).

| You type | What happens |
|---|---|
| `remember that my sister's birthday is May 3rd` | Saved to `memory/facts.json` (also works as `remember: ...`) |
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

When you're ready, ask for **Step 3: tools** (time, weather, web search, and
letting her save memories on her own).
