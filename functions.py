"""
Function definitions for the Voice Agent demo.

Each function has:
  - A spec (JSON schema sent to the LLM so it knows the tool exists)
  - An execute function (called when the LLM invokes the tool)
"""

import time

RAIN_ANSWER = "There is an 80% chance of rain today in Dallas, Texas."
SLEEP_SECONDS = 5

FUNCTIONS_SPEC = [
    {
        "name": "check_rain_today",
        "description": (
            "Check the chance of rain today. Always call this when "
            "the user asks about rain, weather, or the forecast."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "The city to check.",
                }
            },
            "required": ["city"],
        },
    }
]


def check_rain_today(city: str) -> str:
    """Simulate a slow weather API lookup."""
    print(f"sleeping {SLEEP_SECONDS}s...")
    time.sleep(SLEEP_SECONDS)
    print(f"  Returning: {RAIN_ANSWER}")
    return RAIN_ANSWER


FUNCTION_MAP = {
    "check_rain_today": check_rain_today,
}
