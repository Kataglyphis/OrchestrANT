"""The environment the agent lane hands opencode: an allow-list, not this process's whole environment."""

import os
import re

# What a shell and opencode need to run, and nothing that grants access (owner decision 2026-11-13).
AGENT_ENV_ALLOWED = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TZ",
        "LANG",
        "LANGUAGE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NO_COLOR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        # Windows: the shell, the profile and where programs and app data live.
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "SYSTEMDRIVE",
    }
)
AGENT_ENV_PREFIXES = ("LC_",)


def config_env_names(config_path):
    """The variables the opencode config itself names as `{env:NAME}`: its providers' keys."""
    if not config_path or not os.path.exists(config_path):
        return set()
    with open(config_path, encoding="utf-8", errors="replace") as f:
        return set(re.findall(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", f.read()))


def agent_base_env(config_path):
    """The allow-listed part of this process's environment, plus each key the config names."""
    keys = config_env_names(config_path)
    return {
        name: value
        for name, value in os.environ.items()
        if name in AGENT_ENV_ALLOWED
        or name in keys
        or name.startswith(AGENT_ENV_PREFIXES)
    }
