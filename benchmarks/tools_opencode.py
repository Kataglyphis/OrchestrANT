"""An APPROXIMATION of opencode's ten built-in tool schemas, for `--tools opencode`.

These are NOT the bytes captured off the wire in
docs/geniex-local-ai-setup.md#1m-the-end-to-end-agent-run--the-constraint-every-proxy-missed-measured-2026-09-04
(21,144 characters, ~5,286 tokens for the ten schemas). That capture was
never committed, so this file re-creates the set at the same shape and roughly
the same length: the same ten tool names the doc lists (bash, edit, glob, grep,
read, skill, task, todowrite, webfetch, write), the same parameter names, and
descriptions written in the same register and at comparable length. The point
of the option is a realistic preamble -- selection among ten long descriptions
instead of eight one-liners -- not byte fidelity. A run that used this set has
config.tools == "opencode" and config.tools_source == SOURCE so the report
never passes as a measurement against the real schemas.

Replace with the captured file, keep the name, and change SOURCE when that
happens.
"""

import json

SOURCE = "approximation (authored 2026-09-05, not the wire capture)"

# Returned by translate_expect() for a case that has no single defensible
# answer under this tool set; evaluate() skips the case and records it.
UNTRANSLATABLE = object()

_BASH = """Executes a given bash command in a persistent shell session with optional timeout, ensuring proper handling and security measures.

Before executing the command, please follow these steps:

1. Directory Verification:
   - If the command will create new directories or files, first use the glob or read tool to verify the parent directory exists and is the correct location
   - For example, before running "mkdir foo/bar", first check that "foo" exists and is the intended parent directory

2. Command Execution:
   - Always quote file paths that contain spaces with double quotes (e.g., cd "path with spaces/file.txt")
   - Examples of proper quoting:
     - cd "/Users/name/My Documents" (correct)
     - cd /Users/name/My Documents (incorrect - will fail)
     - python "/path/with spaces/script.py" (correct)
   - After ensuring proper quoting, execute the command.
   - Capture the output of the command.

Usage notes:
  - The command argument is required.
  - You can specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). If not specified, commands will time out after 120000ms (2 minutes).
  - It is very helpful if you write a clear, concise description of what this command does in 5-10 words.
  - If the output exceeds 30000 characters, output will be truncated before being returned to you.
  - VERY IMPORTANT: You MUST avoid using search commands like `find` and `grep`. Instead use the grep, glob, or task tools to search. You MUST avoid read tools like `cat`, `head`, `tail`, and `ls`, and use the read and glob tools to read files.
  - If you _still_ need to run `grep`, STOP. ALWAYS USE ripgrep at `rg` first, which all users have pre-installed.
  - When issuing multiple commands, use the ';' or '&&' operator to separate them. DO NOT use newlines (newlines are ok in quoted strings).
  - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the user explicitly requests it.
    <good-example>
    pytest /foo/bar/tests
    </good-example>
    <bad-example>
    cd /foo/bar && pytest tests
    </bad-example>

# Committing changes with git

When the user asks you to create a new git commit, follow these steps carefully:

1. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following bash commands in parallel, each using the bash tool:
  - Run a git status command to see all untracked files.
  - Run a git diff command to see both staged and unstaged changes that will be committed.
  - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.
2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:
  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.).
  - Check for any sensitive information that shouldn't be committed
  - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"
  - Ensure it accurately reflects the changes and their purpose
3. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following commands in parallel:
   - Add relevant untracked files to the staging area.
   - Create the commit with a message.
   - Run git status to make sure the commit succeeded.
4. If the commit fails due to pre-commit hook changes, retry the commit ONCE to include these automated changes. If it fails again, it usually means a pre-commit hook is preventing the commit. If the commit succeeds but you notice that files were modified by the pre-commit hook, you MUST amend your commit to include them.

Important notes:
- NEVER update the git config
- NEVER run additional commands to read or explore code, besides git bash commands
- NEVER use the todowrite or task tools
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC.

# Creating pull requests
Use the gh command via the bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. Run the following bash commands in parallel, in order to understand the current state of the branch since it diverged from the main branch: git status, git diff, check whether the branch tracks a remote and is up to date, git log and git diff [base-branch]...HEAD.
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request summary.
3. Run the following commands in parallel: create a new branch if needed, push to remote with -u flag if needed, and create the PR using gh pr create with a HEREDOC body.

Important:
- NEVER update the git config
- DO NOT use the todowrite or task tools
- Return the PR URL when you're done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""

_EDIT = """Performs exact string replacements in files.

Usage:
- You must use your `read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: spaces + line number + tab. Everything after that tab is the actual file content to match. Never include any part of the line number prefix in the oldString or newString.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `oldString` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replaceAll` to change every instance of `oldString`.
- Use `replaceAll` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
- If the file has Windows line endings (CRLF), match them exactly; a mismatch in line endings is the most common reason an edit reports that oldString was not found even though the text looks identical.
- Make one logical change per call. A call that touches unrelated regions of a file is hard to review and impossible to undo selectively; issue several calls instead.
- Do not attempt to fix an unrelated problem you noticed while editing. Mention it and let the user decide."""

_GLOB = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the task tool instead
- You have the capability to call multiple tools in a single response. It is always better to speculatively perform multiple searches as a batch that are potentially useful.
- Results are capped; if the cap is reached, narrow the pattern or the path rather than assuming the list is complete."""

_GREP = """- Fast content search tool that works with any codebase size
- Searches file contents using regular expressions
- Supports full regex syntax (eg. "log.*Error", "function\\s+\\w+", etc.)
- Filter files by pattern with the include parameter (eg. "*.js", "*.{ts,tsx}")
- Returns file paths and line numbers with at least one match sorted by modification time
- Use this tool when you need to find files containing specific patterns
- If you need to identify/count the number of matches within files, use the bash tool with `rg` (ripgrep) directly. Do NOT use `grep`.
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the task tool instead
- Special characters in the pattern are regex metacharacters: escape a literal dot, bracket or parenthesis, or the search will match more than the user asked for."""

_READ = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The filePath parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Any lines longer than 2000 characters will be truncated
- Results are returned using cat -n format, with line numbers starting at 1
- This tool cannot read binary files, including images
- You have the capability to call multiple tools in a single response. It is always better to speculatively read multiple files as a batch that are potentially useful.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
- Reading a directory returns an error; use glob or bash `ls` for listings."""

_SKILL = """Load a skill to get detailed instructions for a specific task.
Skills provide specialized knowledge and step-by-step guidance for particular workflows: a deploy procedure, a review checklist, a repository-specific build sequence.
Use this when a task matches an available skill's description; the skill's instructions load into the conversation and replace your default approach for that task.
Available skills are listed with a one-line description each. Pass the exact name from that list; do not guess names, and do not invoke a skill for a task it does not describe.
A skill is instructions, not an action: loading one performs nothing on its own."""

_TASK = """Launch a new agent to handle complex, multi-step tasks autonomously.

Available agent types and the tools they have access to:
- general: General-purpose agent for researching complex questions, searching for code, and executing multi-step tasks. When you are searching for a keyword or file and are not confident that you will find the right match in the first few tries use this agent to perform the search for you.
- explore: Fast agent specialized for exploring codebases. Use this when you need to quickly find files by patterns, search code for keywords, or answer questions about the codebase structure.

When using the task tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use the task tool:
- If you want to read a specific file path, use the read or glob tool instead of the task tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the glob tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the read tool instead of the task tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:
1. Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
2. When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
3. Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you.
4. The agent's outputs should generally be trusted
5. Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent"""

_TODOWRITE = """Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool
Use this tool proactively in these scenarios:

1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
3. User explicitly requests todo list - When the user directly asks you to use the todo list
4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
5. After receiving new instructions - Immediately capture user requirements as todos. Feel free to edit the todo list based on new information.
6. After completing a task - Mark it complete and add any new follow-up tasks
7. When you start working on a new task, mark the todo as in_progress. Ideally you should only have one todo as in_progress at a time. Complete existing tasks before starting new ones.

## When NOT to Use This Tool

Skip using this tool when:
1. There is only a single, straightforward task
2. The task is trivial and tracking it provides no organizational benefit
3. The task can be completed in less than 3 trivial steps
4. The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task States and Management

1. **Task States**: Use these states to track progress:
   - pending: Task not yet started
   - in_progress: Currently working on (limit to ONE task at a time)
   - completed: Task finished successfully
   - cancelled: Task no longer needed

2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
   - Only have ONE task in_progress at any time
   - Complete current tasks before starting new ones
   - Cancel tasks that become irrelevant

3. **Task Breakdown**:
   - Create specific, actionable items
   - Break complex tasks into smaller, manageable steps
   - Use clear, descriptive task names

When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully."""

_WEBFETCH = """- Fetches content from a specified URL and returns it in the requested format
- Takes a URL and optional format (markdown, text, or html) as input
- Fetches the URL content, converts HTML to the requested format when asked
- Returns the content in the specified format
- Use this tool when you need to retrieve and analyze web content
- The URL must be a fully-formed valid URL; HTTP URLs will be automatically upgraded to HTTPS
- Results may be summarized if the content is very large
- This tool is read-only and does not modify any files
- Set a timeout for slow hosts; the default is 30 seconds and the maximum is 120"""

_WRITE = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the read tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
- The content parameter is the whole file: a partial write truncates whatever was there. Use the edit tool to change part of a file."""


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS = [
    _tool(
        "bash",
        _BASH,
        {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of what this command does in 5-10 words. "
                "Examples:\nInput: ls\nOutput: Lists files in current directory\n\n"
                "Input: git status\nOutput: Shows working tree status\n\n"
                "Input: npm install\nOutput: Installs package dependencies",
            },
        },
        ["command", "description"],
    ),
    _tool(
        "edit",
        _EDIT,
        {
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "oldString": {"type": "string", "description": "The text to replace"},
            "newString": {
                "type": "string",
                "description": "The text to replace it with (must be different from oldString)",
            },
            "replaceAll": {
                "type": "boolean",
                "description": "Replace all occurrences of oldString (default false)",
            },
        },
        ["filePath", "oldString", "newString"],
    ),
    _tool(
        "glob",
        _GLOB,
        {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. If not specified, the current working directory "
                "will be used. IMPORTANT: Omit this field to use the default directory. DO NOT "
                'enter "undefined" or "null" - simply omit it for the default behavior. Must '
                "be a valid directory path if provided.",
            },
        },
        ["pattern"],
    ),
    _tool(
        "grep",
        _GREP,
        {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. Defaults to the current working directory.",
            },
            "include": {
                "type": "string",
                "description": 'File pattern to include in the search (e.g. "*.js", "*.{ts,tsx}")',
            },
        },
        ["pattern"],
    ),
    _tool(
        "read",
        _READ,
        {
            "filePath": {
                "type": "string",
                "description": "The path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from (0-based)",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read (defaults to 2000)",
            },
        },
        ["filePath"],
    ),
    _tool(
        "skill",
        _SKILL,
        {
            "name": {
                "type": "string",
                "description": "The skill identifier from available_skills (e.g., 'pdf-processing')",
            }
        },
        ["name"],
    ),
    _tool(
        "task",
        _TASK,
        {
            "description": {
                "type": "string",
                "description": "A short (3-5 words) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform",
            },
            "subagent_type": {
                "type": "string",
                "description": "The type of specialized agent to use for this task",
            },
        },
        ["description", "prompt", "subagent_type"],
    ),
    _tool(
        "todowrite",
        _TODOWRITE,
        {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Brief description of the task",
                        },
                        "status": {
                            "type": "string",
                            "description": "Current status of the task: pending, in_progress, "
                            "completed, cancelled",
                            "enum": [
                                "pending",
                                "in_progress",
                                "completed",
                                "cancelled",
                            ],
                        },
                        "priority": {
                            "type": "string",
                            "description": "Priority level of the task: high, medium, low",
                            "enum": ["high", "medium", "low"],
                        },
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for the todo item",
                        },
                    },
                    "required": ["content", "status", "priority", "id"],
                },
            }
        },
        ["todos"],
    ),
    _tool(
        "webfetch",
        _WEBFETCH,
        {
            "url": {"type": "string", "description": "The URL to fetch content from"},
            "format": {
                "type": "string",
                "description": "The format to return the content in (text, markdown, or html)",
                "enum": ["text", "markdown", "html"],
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (max 120)",
            },
        },
        ["url", "format"],
    ),
    _tool(
        "write",
        _WRITE,
        {
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
            },
        },
        ["content", "filePath"],
    ),
]


def approx_tokens():
    return len(json.dumps(TOOLS)) // 4


def translate_expect(expect):
    """The default-set expectation, restated for this tool set.

    Only mappings with ONE defensible answer survive; anything else is
    UNTRANSLATABLE and the case is skipped rather than graded on an opinion.
    A list of expectations translates element-wise.
    """
    if expect is None:
        return None
    if isinstance(expect, list):
        out = [translate_expect(e) for e in expect]
        return UNTRANSLATABLE if any(e is UNTRANSLATABLE for e in out) else out
    name, args = expect["name"], expect["args"]
    if name == "read_file":
        out = {"filePath": args["path"]}
        if "max_lines" in args:
            out["limit"] = args["max_lines"]
        return {"name": "read", "args": out}
    if name == "search_code" and set(args) == {"query"}:
        return {"name": "grep", "args": {"pattern": args["query"]}}
    if name == "write_file":
        return {
            "name": "write",
            "args": {"filePath": args["path"], "content": args["content"]},
        }
    if name == "apply_patch" and "diff" in args:
        removed = [t for t in args["diff"]["contains"] if t.startswith("-")]
        added = [t for t in args["diff"]["contains"] if t.startswith("+")]
        return {
            "name": "edit",
            "args": {
                "filePath": args["path"],
                "oldString": {"contains": [t[1:] for t in removed]},
                "newString": {"contains": [t[1:] for t in added]},
            },
        }
    if name == "run_tests" and set(args) <= {"path"}:
        return {
            "name": "bash",
            "args": {"command": {"contains": ["test"] + list(args.values())}},
        }
    if name == "git_status":
        return {"name": "bash", "args": {"command": {"contains": ["git status"]}}}
    if name == "git_diff":
        return {
            "name": "bash",
            "args": {"command": {"contains": ["git diff"] + list(args.values())}},
        }
    # list_files (glob "*" or bash ls), case_sensitive, include and verbosity
    # have two defensible answers or no parameter here.
    return UNTRANSLATABLE


def translate_history_call(name, args):
    """A concrete (name, args) for a history turn -- this is context, not grading."""
    if name == "read_file":
        return "read", {"filePath": args["path"]}
    if name == "list_files":
        return "bash", {
            "command": f"ls {args['directory']}",
            "description": "Lists directory entries",
        }
    if name == "search_code":
        return "grep", {"pattern": args["query"]}
    if name == "run_tests":
        return "bash", {
            "command": "python -m pytest",
            "description": "Runs the test suite",
        }
    if name == "git_status":
        return "bash", {
            "command": "git status",
            "description": "Shows working tree status",
        }
    if name == "git_diff":
        return "bash", {
            "command": "git diff",
            "description": "Shows uncommitted changes",
        }
    if name == "write_file":
        return "write", {"filePath": args["path"], "content": args["content"]}
    raise KeyError(name)
