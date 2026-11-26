"""
FRIDAY - your personal assistant.

Step 1 taught: calling the model, personality (system prompt), short-term
memory (resending the conversation), and streaming.

Step 2 added long-term memory (see memory.py):
  - "memories"           lists what she knows
  - "forget 3"           deletes fact #3
  - on exit, she writes a short recap of the conversation for next time

Step 3 adds tools (see tools.py) and the agent loop (run_turn below): she can
check the time, the weather, search the web, and save memories on her own.

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
import tools

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

# Your home city, so "what's the weather?" works without naming a place.
HOME_CITY = os.environ.get("FRIDAY_HOME_CITY", "")

# Safety net: the most tool round-trips allowed for a single message.
MAX_TOOL_ROUNDS = 10

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

Your abilities:
- You have tools for the current time, the weather, web searches, and saving
  memories. Use them instead of guessing. For anything current (news, scores,
  prices, store hours), search; don't rely on what you already know.
- When you save a memory, mention it briefly and in character ("Noted, babe.").
- You still CANNOT read files, send messages, or control devices. If asked,
  say plainly that it isn't wired up yet rather than pretending.
""" + (f"\n{USER_NAME}'s home city is {HOME_CITY}. Use it when they don't name a place.\n" if HOME_CITY else "")

RECAP_REQUEST = (
    "[Memory system] This conversation is ending. Write a recap for your own "
    "notes, so future-you remembers what happened. 2-4 sentences, plain text, "
    f"written from your point of view about {USER_NAME}. Keep: what you talked "
    "about, decisions or plans they made, how they were feeling, and anything "
    "you should follow up on next time. Skip small talk. Don't use any tools. "
    "Output only the recap."
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
        # The same tool list goes on every request in a conversation.
        tools=tools.TOOLS,
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        for event in stream:
            if not stream_to_screen:
                continue
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                print(event.delta.text, end="", flush=True)
            elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                # Server tools (web search) run on Anthropic's side mid-reply.
                print(f"\n  [{tools.STATUS.get(event.content_block.name, 'working')}...]\n", end="", flush=True)
        return stream.get_final_message()


class TurnFailed(Exception):
    """The turn couldn't finish; the caller rolls the history back."""


def run_turn(client, system_prompt, history) -> None:
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
        reply = call_friday(client, system_prompt, history, stream_to_screen=True)

        if reply.stop_reason == "refusal":
            raise TurnFailed("FRIDAY declined that request.")
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
        print("FRIDAY: ", end="", flush=True)

    raise TurnFailed("She got stuck in a loop of tool calls.")


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
    print("        (exit, reset, memories, forget <n>)\n")

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

        # If anything fails mid-turn, we cut history back to this point so it
        # never holds half a turn (like a tool request with no result).
        turn_start = len(history)
        history.append({"role": "user", "content": user_text})

        print("FRIDAY: ", end="", flush=True)
        try:
            # ---------------------------------------------------------------
            # 4. THE AGENT LOOP (model calls + tool calls)
            # ---------------------------------------------------------------
            run_turn(client, system_prompt, history)
        except TurnFailed as e:
            print(f"\n[{e}]\n")
            del history[turn_start:]
        except anthropic.AuthenticationError:
            print("\n[!] API key missing or invalid. Set ANTHROPIC_API_KEY.")
            sys.exit(1)
        except anthropic.RateLimitError:
            print("\n[!] Rate limited. Wait a moment and try again.\n")
            del history[turn_start:]  # forget the unfinished turn
            continue
        except anthropic.APIConnectionError:
            print("\n[!] Can't reach the API. Check your internet connection.\n")
            del history[turn_start:]
            continue
        except anthropic.APIStatusError as e:
            print(f"\n[!] API error {e.status_code}: {e.message}\n")
            del history[turn_start:]
            continue
        except KeyboardInterrupt:
            print("\n[interrupted]\n")
            del history[turn_start:]
            continue


if __name__ == "__main__":
    main()
