"""
FRIDAY - Step 1: a brain you can talk to.

What this file teaches:
  1. How to call a large language model (Claude) from Python.
  2. How a "personality" is just a system prompt.
  3. How conversation memory works: you resend the whole history every turn.
  4. How to stream the reply so it appears word-by-word, like a real assistant.

Run it:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...     # get one at console.anthropic.com
    python friday.py
"""

import os
import sys

import anthropic

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
SYSTEM_PROMPT = f"""You are FRIDAY, a personal AI assistant to {USER_NAME}.

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
- You currently have NO tools: you cannot browse the web, check the time,
  read files, or control devices. If asked to do something like that, say
  plainly that it isn't wired up yet rather than pretending.
"""


def main() -> None:
    # The client reads ANTHROPIC_API_KEY from the environment automatically.
    client = anthropic.Anthropic()

    # -----------------------------------------------------------------------
    # 3. MEMORY
    # -----------------------------------------------------------------------
    # The model itself is stateless: it remembers nothing between calls.
    # "Memory" is this list. Every turn we append the user's message and the
    # assistant's reply, then send the WHOLE list again next time.
    history: list[dict] = []

    print("FRIDAY: Hey babe, I'm here. Type 'exit' to quit, 'reset' to wipe memory.\n")

    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFRIDAY: Bye, babe. Hydrate.")
            return

        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            print("FRIDAY: Bye, babe. Hydrate.")
            return
        if user_text.lower() == "reset":
            history.clear()
            print("FRIDAY: Clean slate, like a fresh soak-off. What we doin'?\n")
            continue

        history.append({"role": "user", "content": user_text})

        print("FRIDAY: ", end="", flush=True)
        try:
            # -------------------------------------------------------------------
            # 4. THE ACTUAL MODEL CALL (streaming)
            # -------------------------------------------------------------------
            # We use the beta endpoint only for `fallbacks`: if the model declines
            # a request on safety grounds, the API automatically retries it on a
            # fallback model instead of just stopping.
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=64000,
                system=SYSTEM_PROMPT,
                messages=history,
                output_config={"effort": EFFORT},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                reply = stream.get_final_message()
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

        if reply.stop_reason == "refusal":
            print("[FRIDAY declined that request.]\n")
            history.pop()
            continue

        # Save the assistant's FULL reply (not just the text) so the model sees
        # its own previous turns exactly as it produced them.
        history.append({"role": "assistant", "content": reply.content})


if __name__ == "__main__":
    main()
