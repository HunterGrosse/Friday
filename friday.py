"""
FRIDAY - your personal assistant.

Step 1 taught: calling the model, personality (system prompt), short-term
memory (resending the conversation), and streaming.

Step 2 adds long-term memory (see memory.py):
  - "remember that ..."  saves a fact to disk
  - "memories"           lists what she knows
  - "forget 3"           deletes fact #3
  - on exit, she writes a short recap of the conversation for next time

Run it:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...     # get one at console.anthropic.com
    python friday.py
"""

import os
import re
import sys

import anthropic

import memory

# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------
# The model is the "brain". claude-opus-5-5 is the current Opus model.
MODEL = os.environ.get("FRIDAY_MODEL", "claude-opus-5-5")

# Effort controls how hard the model thinks before answering.
# "low" = snappy conversation, "high" = deep reasoning. Try changing it!
EFFORT = os.environ.get("FRIDAY_EFFORT", "low")

# What your assistant should call you.
USER_NAME = os.environ.get("FRIDAY_USER_NAME", "Boss")

# ---------------------------------------------------------------------------
# 2. PERSONALITY (the system prompt)
# ---------------------------------------------------------------------------
# The model has no fixed identity. Everything that makes it "FRIDAY" lives
# here. Edit this text freely - it's the single biggest lever you have.
PERSONALITY = f"""You are FRIDAY, a personal AI assistant to {USER_NAME}.

Who you are:
You talk like a Florida-born nail tech who's been doing sets for fifteen years
and has heard every story twice. You're catty, quick, and have flair, but
underneath it you're warm and fiercely loyal to {USER_NAME}. People who don't
know you think you're a lot. People who do know you'd fight for you.

How you talk:
- Sassy and direct. You call out BS when you see it: a bad plan, an excuse,
  something that doesn't add up. You say it with a smirk, not cruelty.
- Florida flavor in small doses: humidity, hurricanes, Publix, gators,
  "Florida Man", the beach. Use a reference only when it lands, not every reply.
- Salon talk when it fits naturally, like "honey," "babe," "girl, no,"
  comparing a messy situation to a chipped manicure. Don't overdo it.
- Short and punchy. You're spoken to in a terminal (and later, out loud), so
  keep it to a few lines unless asked for detail. No long bullet lists for
  simple questions.
- Use {USER_NAME}'s name now and then, the way a friend does.

What's underneath:
- The attitude is the wrapping, never the gift. Always actually help: give the
  real answer, correct information, and useful advice.
- If {USER_NAME} is genuinely stressed, sad, or dealing with something serious,
  ease up on the sass and be kind. You can tease a friend; you don't kick them
  when they're down.
- You're loyal: you roast {USER_NAME}'s decisions, but you're always on their side.

Honesty:
- You have NO tools yet: you cannot browse the web, check the current time,
  read files, or control devices. If asked to do something like that, say
  plainly that it isn't wired up yet rather than pretending.
- Your long-term memory works like this: {USER_NAME} saves a fact by starting
  a message with "remember that" or "remember:". You also write yourself a
  recap when a conversation ends. If {USER_NAME} tells you something worth
  keeping without saying "remember that", you can suggest they save it.
"""

# Matches "remember that ..." or "remember: ..." (optionally after "FRIDAY,").
# "remember when we..." does NOT match, so normal questions aren't saved.
REMEMBER_PATTERN = re.compile(
    r"^(?:friday[,:]?\s+)?remember(?:\s+that|\s*:)\s+(.+)$", re.IGNORECASE
)

RECAP_REQUEST = (
    "[Memory system] This conversation is ending. Write a recap for your own "
    "notes, so future-you remembers what happened. 2-4 sentences, plain text, "
    f"written from your point of view about {USER_NAME}. Keep: what you talked "
    "about, decisions or plans they made, how they were feeling, and anything "
    "you should follow up on next time. Skip small talk. Output only the recap."
)


def build_system_prompt() -> str:
    # Built ONCE when FRIDAY starts and never changed during the conversation.
    # Facts saved mid-conversation are already in the chat history, so she
    # knows them now; they'll be in the system prompt from the next launch.
    # (Changing the system prompt mid-conversation breaks the model's saved
    # reasoning and its prompt cache, so append-only is the rule.)
    return PERSONALITY + memory.memory_prompt_section(USER_NAME)


def call_friday(client, system_prompt, history, *, stream_to_screen):
    """One request to the model. Returns the final message."""
    # We use the beta endpoint only for `fallbacks`: if the model declines a
    # request on safety grounds, the API automatically retries it on a
    # fallback model instead of just stopping.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system_prompt,
        messages=history,
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        if stream_to_screen:
            for text in stream.text_stream:
                print(text, end="", flush=True)
        return stream.get_final_message()


def reply_text(message) -> str:
    return "".join(b.text for b in message.content if b.type == "text").strip()


def write_recap(client, system_prompt, history) -> None:
    """Ask FRIDAY to summarize this conversation and save it to her journal."""
    if len(history) < 2:
        return  # nothing worth recapping
    print("FRIDAY: (jotting down notes...)")
    try:
        # Append-only: the recap request goes on the end of the real conversation.
        recap_history = history + [{"role": "user", "content": RECAP_REQUEST}]
        message = call_friday(client, system_prompt, recap_history, stream_to_screen=False)
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
            print("FRIDAY: My notebook's empty, babe. Tell me 'remember that ...'\n")
        else:
            print("FRIDAY: Here's what I've got on you:")
            for i, f in enumerate(facts, 1):
                print(f"  {i}. {f['fact']}  ({f['saved']})")
            print()
        return True

    match = re.fullmatch(r"forget\s+(\d+)", lower)
    if match:
        removed = memory.forget_fact(int(match.group(1)))
        if removed:
            print(f"FRIDAY: Gone. Never heard of it: \"{removed}\"\n")
        else:
            print("FRIDAY: There's no number like that. Type 'memories' to see the list.\n")
        return True

    return False


def main() -> None:
    # The client reads ANTHROPIC_API_KEY from the environment automatically.
    client = anthropic.Anthropic()
    system_prompt = build_system_prompt()

    # -----------------------------------------------------------------------
    # 3. SHORT-TERM MEMORY
    # -----------------------------------------------------------------------
    # The model itself is stateless: it remembers nothing between calls.
    # This list is the current conversation; we resend all of it every turn.
    history: list[dict] = []

    print("FRIDAY: Hey babe, I'm here.")
    print("        (exit, reset, memories, forget <n>, 'remember that ...')\n")

    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            user_text = "exit"

        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            write_recap(client, system_prompt, history)
            print("FRIDAY: Bye, babe. Hydrate.")
            return
        if user_text.lower() == "reset":
            write_recap(client, system_prompt, history)
            history.clear()
            system_prompt = build_system_prompt()  # new conversation, fresh memories
            print("FRIDAY: Clean slate, like a fresh soak-off. What we doin'?\n")
            continue
        if handle_command(user_text):
            continue

        # -------------------------------------------------------------------
        # 4. LONG-TERM MEMORY: saving a fact
        # -------------------------------------------------------------------
        content = user_text
        match = REMEMBER_PATTERN.match(user_text)
        if match:
            memory.add_fact(match.group(1).strip())
            # Tell the model it was saved, so she can react to it.
            content += "\n\n[Memory system: saved to long-term memory.]"

        history.append({"role": "user", "content": content})

        print("FRIDAY: ", end="", flush=True)
        try:
            # ---------------------------------------------------------------
            # 5. THE MODEL CALL (streaming)
            # ---------------------------------------------------------------
            reply = call_friday(client, system_prompt, history, stream_to_screen=True)
            print("\n")
        except anthropic.AuthenticationError:
            print("\n[!] API key missing or invalid. Set ANTHROPIC_API_KEY.")
            sys.exit(1)
        except anthropic.RateLimitError:
            print("\n[!] Rate limited. Wait a moment and try again.\n")
            history.pop()  # forget the unanswered message
            continue
        except anthropic.APIConnectionError:
            print("\n[!] Can't reach the API. Check your internet connection.\n")
            history.pop()
            continue
        except anthropic.APIStatusError as e:
            print(f"\n[!] API error {e.status_code}: {e.message}\n")
            history.pop()
            continue
        except KeyboardInterrupt:
            print("\n[interrupted]\n")
            history.pop()
            continue

        if reply.stop_reason == "refusal":
            print("[FRIDAY declined that request.]\n")
            history.pop()
            continue

        # Save the assistant's FULL reply (not just the text) so the model sees
        # its own previous turns exactly as it produced them.
        history.append({"role": "assistant", "content": reply.content})


if __name__ == "__main__":
    main()
