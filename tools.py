"""
Zoey's tools (Step 3).

A tool has two halves:

  1. A DESCRIPTION we send to the model: name, what it does, and what inputs it
     takes (as a JSON schema). The model reads these and decides when to use one.
  2. A PYTHON FUNCTION that actually does the work, which WE run when the model
     asks for it.

The model never runs anything itself. It replies "please call get_weather with
city=Tampa", our code runs the function, and we send the result back. That
back-and-forth is called the agent loop (see run_turn in zoey.py).

There's one exception: web_search is a *server tool*. Anthropic runs it on
their side, so there's no Python function for it here.

To add a new ability: write a function, describe it in TOOLS, and add it to
TOOL_FUNCTIONS. That's it.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime

import memory


# ---------------------------------------------------------------------------
# The functions (the "hands")
# ---------------------------------------------------------------------------
def get_current_time() -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A, %B %d, %Y, %I:%M %p %Z")


WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms with hail",
}


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def get_weather(city: str) -> str:
    # Open-Meteo is free and needs no API key. Step 1: city name -> coordinates.
    geo = _get_json(
        "https://geocoding-api.open-meteo.com/v1/search?"
        + urllib.parse.urlencode({"name": city, "count": 1})
    )
    if not geo.get("results"):
        raise ValueError(f"Couldn't find a place called {city!r}.")
    place = geo["results"][0]

    # Step 2: coordinates -> current weather and today's forecast.
    data = _get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode({
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "timezone": "auto",
            "forecast_days": 1,
        })
    )
    now, today = data["current"], data["daily"]
    where = ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
    return (
        f"{where}: {WEATHER_CODES.get(now['weather_code'], 'unknown conditions')}, "
        f"{now['temperature_2m']:.0f}°C (feels like {now['apparent_temperature']:.0f}°C), "
        f"humidity {now['relative_humidity_2m']}%, wind {now['wind_speed_10m']:.0f} km/h. "
        f"Today: high {today['temperature_2m_max'][0]:.0f}°C, low {today['temperature_2m_min'][0]:.0f}°C, "
        f"{today['precipitation_probability_max'][0] or 0}% chance of rain."
    )


def save_memory(fact: str) -> str:
    memory.add_fact(fact)
    return "Saved to long-term memory."


TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
    "get_weather": get_weather,
    "save_memory": save_memory,
}


# ---------------------------------------------------------------------------
# The descriptions (what the model sees)
# ---------------------------------------------------------------------------
# The description text matters a lot: it's how the model decides WHEN to use a
# tool. Write it like you're explaining the tool to a new coworker.
TOOLS = [
    {
        "name": "get_current_time",
        "description": "Get the current local date and time. Use whenever the "
                       "answer depends on what time or day it is right now.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "get_weather",
        "description": "Get current weather and today's forecast for a city, in "
                       "Celsius. Use for any question about weather, "
                       "temperature, rain, or what to wear outside.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Tampa' or 'Paris, France'"},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "save_memory",
        "description": "Save a fact about the user to long-term memory so you "
                       "still know it in future conversations. Use when they "
                       "ask you to remember something, or share something "
                       "lasting and personal (people in their life, birthdays, "
                       "preferences, goals, important dates, where they live or "
                       "work). Don't save small talk or things only relevant "
                       "today. Write the fact as a short, complete sentence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "e.g. 'Their sister Kayla's birthday is May 3rd.'"},
            },
            "required": ["fact"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    # Server tool: runs on Anthropic's servers. Costs extra per search
    # (see anthropic.com/pricing), so it's capped at 5 searches per message.
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
]

# What to show on screen while a tool runs, so you can watch her work.
STATUS = {
    "get_current_time": "checking the clock",
    "get_weather": "checking the weather",
    "save_memory": "writing that down",
    "web_search": "searching the web",
    "code_execution": "sorting through results",  # web search's built-in filtering
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run one tool. Returns (result_text, is_error)."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return f"Unknown tool: {name}", True
    try:
        return function(**tool_input), False
    except Exception as e:  # tell the model what went wrong so it can adapt
        return f"Tool failed: {e}", True
