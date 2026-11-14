"""A server's tool call read the same way by every grader of the tools lane; a malformed one is a failure, not a raise."""

import json


def calls_of(message):
    """The reply's tool calls as a list; anything else a server put there counts as one malformed call."""
    if not isinstance(message, dict):
        return []
    calls = message.get("tool_calls")
    if not calls:
        return []
    return calls if isinstance(calls, list) else [calls]


def call_parts(call):
    """(name, raw arguments, error) of one call; the error names what is malformed about it."""
    if not isinstance(call, dict):
        return None, None, f"a tool call that is no object: {call!r:.60}"
    function = call.get("function")
    if not isinstance(function, dict):
        return None, None, f"a tool call whose function is no object: {function!r:.60}"
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return (
            None,
            function.get("arguments"),
            f"a tool call without a name: {name!r:.60}",
        )
    return name, function.get("arguments"), None


def parse_args(raw):
    """(args, error). Servers differ on whether arguments arrive as text or a dict; only an object is arguments."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"arguments are not valid JSON: {e}"
    if not isinstance(raw, dict):
        return None, f"arguments are not a JSON object: {raw!r:.60}"
    return raw, None


def reply_message(choice):
    """The choice's message, or {} for a missing or malformed one."""
    message = choice.get("message") if isinstance(choice, dict) else None
    return message if isinstance(message, dict) else {}
