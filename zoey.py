"""
Zoey - your personal assistant.

Step 1 taught: calling the model, personality (system prompt), short-term
memory (resending the conversation), and streaming.

Step 2 added long-term memory (see memory.py):
  - "memories"           lists what she knows
  - "forget 3"           deletes fact #3
  - on exit, she writes a short recap of the conversation for next time

Step 3 added tools (see tools.py) and the agent loop (run_turn below): she can
check the time, the weather, search the web, and save memories on her own.

Step 4 added her voice (see voice.py):   python zoey.py --voice

Step 5 makes her always-on on a Mac (see zoeyctl): keys live in .env, she
starts at login, restarts if she crashes, and starts a fresh conversation
after she's been idle a while.

Run it:
    pip install -r requirements.txt
    cp .env.example .env        # then fill in your keys
    python zoey.py              # type to her
    python zoey.py --voice      # talk to her
"""

import os
import re
import signal
import sys
import time
from pathlib import Path


def load_env_file(path: Path = Path(__file__).parent / ".env") -> None:
    """Read KEY=value lines from .env into the environment (real env wins)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = re.split(r"(?:^|\s)#", value, maxsplit=1)[0]   # drop "# comments"
        value = value.strip().strip("'\"")
        if value:  # leave blank settings unset
            os.environ.setdefault(key, value)


load_env_file()  # must run before anything below reads os.environ

import anthropic

import memory
import tools

# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------
# The model is the "brain". claude-opus-5-5 is the current Opus model.
MODEL = os.environ.get("ZOEY_MODEL", "claude-opus-5-5")

# Effort controls how hard the model thinks before answering.
# "low" = snappy conversation, "high" = deep reasoning. Try changing it!
EFFORT = os.environ.get("ZOEY_EFFORT", "low")

# What your assistant should call you.
USER_NAME = os.environ.get("ZOEY_USER_NAME", "Boss")

# Your home city, so "what's the weather?" works without naming a place.
HOME_CITY = os.environ.get("ZOEY_HOME_CITY", "")

# Safety net: the most tool round-trips allowed for a single message.
MAX_TOOL_ROUNDS = 10

# Voice mode: after this many quiet minutes she files the conversation away
# (writes her recap) and the next "Hey Zoey" starts fresh, with today's date
# and her latest memories.
SESSION_IDLE_MINUTES = float(os.environ.get("ZOEY_SESSION_IDLE_MINUTES", "15"))

# ---------------------------------------------------------------------------
# 2. PERSONALITY (the system prompt)
# ---------------------------------------------------------------------------
# The model has no fixed identity. Everything that makes it "Zoey" lives in
# personality.md. Edit it freely, even while she's running: she picks up the
# new version at the start of her next conversation. {name} becomes your name.
PERSONALITY_FILE = Path(__file__).parent / "personality.md"


def personality_version() -> float:
    """Changes whenever personality.md is saved."""
    return PERSONALITY_FILE.stat().st_mtime


def load_personality() -> str:
    text = PERSONALITY_FILE.read_text(encoding="utf-8").replace("{name}", USER_NAME)
    if HOME_CITY:
        text += f"\n{USER_NAME}'s home city is {HOME_CITY}. Use it when they don't name a place.\n"
    return text


RECAP_REQUEST = (
    "[Memory system] This conversation is ending. Write a recap for your own "
    "notes, so future-you remembers what happened. 2-4 sentences, plain text, "
    f"written from your point of view about {USER_NAME}. Keep: what you talked "
    "about, decisions or plans they made, how they were feeling, and anything "
    "you should follow up on next time. Skip small talk. Don't use any tools. "
    "Output only the recap."
)


VOICE_RULES = f"""
You are SPEAKING OUT LOUD right now, through a speaker. Everything you write is
read aloud by a text-to-speech voice, so:
- Talk like a person on the phone: usually 1-3 sentences. Offer to say more
  rather than monologuing.
- No markdown, bullet points, emojis, URLs, or source citations.
- Write numbers and symbols the way you'd say them: "twelve degrees",
  "about five bucks", "three thirty in the afternoon".
- What you hear from {USER_NAME} is a speech-to-text transcript, so it may have
  small mistakes. Go with the obvious meaning; if it's genuinely unclear, ask.
"""


def build_system_prompt(voice_mode: bool = False) -> str:
    # Built ONCE when Zoey starts and never changed during the conversation.
    # Facts saved mid-conversation are already in the chat history, so she
    # knows them now; they'll be in the system prompt from the next launch.
    # (Changing the system prompt mid-conversation breaks the model's saved
    # reasoning and its prompt cache, so append-only is the rule.)
    prompt = load_personality() + memory.memory_prompt_section(USER_NAME)
    return prompt + VOICE_RULES if voice_mode else prompt


def require_api_key() -> None:
    # Without this, a missing key only shows up as a traceback on your first
    # message. Exit status 0 = "a setting needs fixing", so the background
    # service doesn't restart her every 10 seconds forever.
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("[!] No API key. Put ANTHROPIC_API_KEY in .env (see README).")
        sys.exit(0)


def print_text(text: str) -> None:
    print(text, end="", flush=True)


def call_zoey(client, system_prompt, history, on_text=None, on_break=None):
    """
    One request to the model. Returns the final message.
    on_text gets each piece of her reply as it streams in (print it, speak it...).
    on_break is called when a new block of text starts (used by the voice).
    """
    # We use the beta endpoint only for `fallbacks`: if the model declines a
    # request on safety grounds, the API automatically retries it on a
    # fallback model instead of just stopping.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system_prompt,
        messages=history,
        # The same tool list goes on every request in a conversation.
        tools=tools.TOOLS,
        # Prompt caching: the API remembers the start of the conversation
        # between requests, so long chats stay fast and cheap.
        cache_control={"type": "ephemeral"},
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        for event in stream:
            if on_text is None:
                continue
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                on_text(event.delta.text)
            elif event.type == "content_block_start" and event.content_block.type == "text" and on_break:
                on_break()
            elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                # Server tools (web search) run on Anthropic's side mid-reply.
                print(f"\n  [{tools.STATUS.get(event.content_block.name, 'working')}...]\n", end="", flush=True)
        return stream.get_final_message()


class TurnFailed(Exception):
    """The turn couldn't finish; the caller rolls the history back."""


def run_turn(client, system_prompt, history, on_text=print_text, on_break=None) -> None:
    """
    THE AGENT LOOP. One user message can take several model calls:

      model replies "use get_weather(city=Tampa)"   (stop_reason: tool_use)
        -> we run get_weather, send the result back
      model replies with the actual answer           (stop_reason: end_turn)
        -> done

    Every model reply and every tool result is appended to history, so the
    model always sees the full story of what it did and what came back.
    """
    for _ in range(MAX_TOOL_ROUNDS):
        reply = call_zoey(client, system_prompt, history, on_text, on_break)

        if reply.stop_reason == "refusal":
            raise TurnFailed("Zoey declined that request.")
        if reply.stop_reason == "max_tokens":
            raise TurnFailed("Her reply got cut off (too long).")

        # Save the assistant's FULL reply (text, tool requests, thinking) so the
        # model sees its own previous turns exactly as it produced them.
        history.append({"role": "assistant", "content": reply.content})

        if reply.stop_reason == "pause_turn":
            # A long web search paused partway; sending the history back as-is
            # tells the server to pick up where it left off.
            continue
        if reply.stop_reason != "tool_use":
            print("\n")
            return  # end_turn: she's finished answering

        # She asked for one or more tools. Run each and collect ALL results
        # into a single user message.
        results = []
        for block in reply.content:
            if block.type != "tool_use":
                continue
            print(f"\n  [{tools.STATUS.get(block.name, block.name)}...]", flush=True)
            output, is_error = tools.run_tool(block.name, block.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,  # links this result to her request
                "content": output,
                "is_error": is_error,
            })
        history.append({"role": "user", "content": results})

    raise TurnFailed("She got stuck in a loop of tool calls.")


def reply_text(message) -> str:
    return "".join(b.text for b in message.content if b.type == "text").strip()


def write_recap(client, system_prompt, history) -> None:
    """Ask Zoey to summarize this conversation and save it to her journal."""
    if len(history) < 2:
        return  # nothing worth recapping
    print("Zoey: (jotting down notes...)")
    try:
        # Append-only: the recap request goes on the end of the real conversation.
        recap_history = history + [{"role": "user", "content": RECAP_REQUEST}]
        message = call_zoey(client, system_prompt, recap_history)
        recap = reply_text(message)
        if message.stop_reason != "refusal" and recap:
            memory.add_recap(recap)
    except (anthropic.APIError, KeyboardInterrupt):
        print("[!] Couldn't save notes for this conversation.")


def handle_command(user_text: str) -> bool:
    """Memory commands that don't need the model. Returns True if handled."""
    lower = user_text.lower()

    if lower == "memories":
        facts = memory.load_facts()
        if not facts:
            print("Zoey: My notebook's empty, babe. Tell me 'remember that ...'\n")
        else:
            print("Zoey: Here's what I've got on you:")
            for i, f in enumerate(facts, 1):
                print(f"  {i}. {f['fact']}  ({f['saved']})")
            print()
        return True

    match = re.fullmatch(r"forget\s+(\d+)", lower)
    if match:
        removed = memory.forget_fact(int(match.group(1)))
        if removed:
            print(f"Zoey: Gone. Never heard of it: \"{removed}\"\n")
        else:
            print("Zoey: There's no number like that. Type 'memories' to see the list.\n")
        return True

    return False


def main() -> None:
    # The client reads ANTHROPIC_API_KEY from the environment automatically.
    require_api_key()
    client = anthropic.Anthropic()
    system_prompt = build_system_prompt()

    # -----------------------------------------------------------------------
    # 3. SHORT-TERM MEMORY
    # -----------------------------------------------------------------------
    # The model itself is stateless: it remembers nothing between calls.
    # This list is the current conversation; we resend all of it every turn.
    history: list[dict] = []
    version = personality_version()

    print("Zoey: Hey babe, I'm here.")
    print("        (exit, reset, memories, forget <n>)\n")

    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            user_text = "exit"

        if not user_text:
            continue
        if personality_version() != version:
            # personality.md was edited: file this conversation away and start
            # a fresh one with the new personality (never edit a live prompt).
            version = personality_version()
            write_recap(client, system_prompt, history)
            history.clear()
            system_prompt = build_system_prompt()
            print("[personality updated; fresh conversation]")
        if user_text.lower() in {"exit", "quit"}:
            write_recap(client, system_prompt, history)
            print("Zoey: Bye, babe. Hydrate.")
            return
        if user_text.lower() == "reset":
            write_recap(client, system_prompt, history)
            history.clear()
            system_prompt = build_system_prompt()  # new conversation, fresh memories
            print("Zoey: Clean slate, like a fresh soak-off. What we doin'?\n")
            continue
        if handle_command(user_text):
            continue

        respond(client, system_prompt, history, user_text)


def respond(client, system_prompt, history, user_text, on_text=print_text, on_break=None) -> bool:
    """Run one full turn for a user message. Returns True if it succeeded."""
    # Print "Zoey: " when her first words arrive, so tool status lines
    # ("[checking the weather...]") show up above her reply, not inside it.
    started = False

    def say(text: str) -> None:
        nonlocal started
        if not started:
            print("Zoey: ", end="", flush=True)
            started = True
        on_text(text)

    # If anything fails mid-turn, we cut history back to this point so it
    # never holds half a turn (like a tool request with no result).
    turn_start = len(history)
    history.append({"role": "user", "content": user_text})
    try:
        # -------------------------------------------------------------------
        # 4. THE AGENT LOOP (model calls + tool calls)
        # -------------------------------------------------------------------
        run_turn(client, system_prompt, history, say, on_break)
        return True
    except TurnFailed as e:
        print(f"\n[{e}]\n")
    except anthropic.AuthenticationError:
        print("\n[!] API key invalid. Check ANTHROPIC_API_KEY in .env.")
        sys.exit(0)  # a setting needs fixing; restarting won't help
    except anthropic.RateLimitError:
        print("\n[!] Rate limited. Wait a moment and try again.\n")
    except anthropic.APIConnectionError:
        print("\n[!] Can't reach the API. Check your internet connection.\n")
    except anthropic.APIStatusError as e:
        print(f"\n[!] API error {e.status_code}: {e.message}\n")
    except KeyboardInterrupt:
        print("\n[interrupted]\n")
    del history[turn_start:]  # forget the unfinished turn
    return False


def voice_main() -> None:
    """Talk to Zoey out loud. Same brain, tools and memory as text mode."""
    import voice  # only needed (and only installed) for voice mode

    require_api_key()
    client = anthropic.Anthropic()
    system_prompt = build_system_prompt(voice_mode=True)
    history: list[dict] = []
    last_activity = time.monotonic()
    version = personality_version()

    try:
        mouth = voice.Mouth()
        ears = voice.Ears()
    except Exception as e:
        # A setup problem (missing key, no mic). Exiting with status 0 tells
        # the background service not to keep restarting until it's fixed.
        print(f"[!] Can't start voice mode: {e}")
        sys.exit(0)

    # Push-to-talk reads Enter from the keyboard (or the Mac app's Talk button,
    # which sets ZOEY_APP). Running as a background service has neither.
    if not ears.hands_free and not sys.stdin.isatty() and not os.environ.get("ZOEY_APP"):
        print("[!] Running in the background needs the wake word. Set "
              "PICOVOICE_ACCESS_KEY and PORCUPINE_KEYWORD_PATH in .env.")
        sys.exit(0)

    # When the Mac logs out or the service is stopped, macOS sends SIGTERM.
    # Treat it like Ctrl+C so she still writes her recap.
    def on_terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_terminate)

    # Stop the Mac from idle-sleeping while she's running (macOS only).
    # caffeinate -w quits by itself when Zoey does.
    if sys.platform == "darwin" and os.environ.get("ZOEY_KEEP_AWAKE", "1") == "1":
        import subprocess
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    def print_and_speak(text: str) -> None:
        print_text(text)
        mouth.feed(text)

    print("Zoey: Hey babe, I'm here.  (Ctrl+C to quit)\n")
    if not os.environ.get("ZOEY_NO_GREETING"):   # the app skips it on reloads
        mouth.say("Hey babe, I'm here.")

    try:
        while True:
            # Sleep until "Hey Zoey", checking once a minute whether it's time
            # to wrap up the current conversation.
            while not ears.wait_for_wake_word(timeout=60):
                idle_minutes = (time.monotonic() - last_activity) / 60
                if history and idle_minutes >= SESSION_IDLE_MINUTES:
                    write_recap(client, system_prompt, history)
                    history.clear()
                    system_prompt = build_system_prompt(voice_mode=True)
                    print("[conversation filed away; next one starts fresh]")

            if personality_version() != version:
                # personality.md was edited: start fresh with the new one.
                version = personality_version()
                write_recap(client, system_prompt, history)
                history.clear()
                system_prompt = build_system_prompt(voice_mode=True)
                print("[personality updated; fresh conversation]")

            # A conversation: after she answers, keep listening a few seconds
            # for a follow-up, so you don't need the wake word every time.
            follow_up = False
            while True:
                audio = ears.listen(wait_for_speech=5 if follow_up else 8)
                if audio is None:
                    break  # you went quiet; back to sleep
                user_text = ears.transcribe(audio)
                if not user_text:
                    break
                print(f"You: {user_text}")

                if respond(client, system_prompt, history, user_text,
                           print_and_speak, mouth.break_sentence):
                    mouth.finish()          # wait until she's done talking
                else:
                    mouth.stop()            # don't finish a broken sentence
                ears.flush()                # don't "hear" her own voice
                follow_up = True
                last_activity = time.monotonic()
    except KeyboardInterrupt:
        mouth.stop()
        print()
    finally:
        write_recap(client, system_prompt, history)
        print("Zoey: Bye, babe. Hydrate.")
        mouth.say("Bye, babe. Hydrate.")
        ears.close()
        mouth.close()


if __name__ == "__main__":
    if "--voice" in sys.argv:
        voice_main()
    else:
        main()
