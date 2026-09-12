# Tool selection rules

Written for small on-device models, and measured rather than guessed.

The QAIRT `Qwen3-4B-Instruct-2507` bundle separates tools by their **description
text**, not by their names, and it has two reproducible failure modes that this
file fixes (8/12 → 12/12 on `bench_tools.py`):

1. it reached for a *list* tool when asked for a file's **contents**, and
2. it sometimes emitted the arguments as a JSON object **in its reply text**
   instead of as a tool call — which `tool_choice: "required"` does *not* fix.

Runtimes whose built-in tool descriptions cannot be overridden (opencode among
them) can still deliver the same disambiguation through the system prompt. That
is what this file is for.

---

When choosing a tool, apply these rules:

- A tool that **reads a file** returns that file's **CONTENTS**. Use it whenever
  the request is about what is *in* a file — "show me", "what does X contain",
  "read", "open".
- A tool that **lists a directory** returns only file **NAMES**. It never
  returns contents. Use it only when the request is about *which* files exist.
- A tool that **searches** takes the literal text to look for. Extract that text
  from the request exactly as written, without rephrasing it.
- Optional parameters are not optional when the user asks for them. If the
  request says "exactly", "case-sensitive", "recursively", set the matching
  parameter explicitly rather than relying on a default.

Always emit an actual **tool call**. Never write the arguments as a JSON object
in your reply text: a JSON object in prose is not a tool call and the caller
cannot act on it.

If no tool is needed — a question you can answer directly — answer directly and
call nothing. Reaching for a tool on every turn wastes a round trip.
