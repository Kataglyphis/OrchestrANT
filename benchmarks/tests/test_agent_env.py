"""The agent lane's environment: the allow-list and the config's own keys, nothing else."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent_env


class TestAgentEnv:
    def test_env_holds_the_allow_list_and_the_configs_own_key_only(
        self, monkeypatch, tmp_path
    ):
        config = tmp_path / "opencode.jsonc"
        config.write_text(
            '{"provider": {"lab": {"options": {"apiKey": "{env:LAB_API_KEY}"}}}}'
        )
        for name, value in {
            "LAB_API_KEY": "lab",
            "OPENAI_API_KEY": "other",
            "HF_TOKEN": "hf",
            "GH_TOKEN": "gh",
            "PATH": "/usr/bin",
            "LC_ALL": "C.UTF-8",
            "HTTPS_PROXY": "http://proxy:3128",
        }.items():
            monkeypatch.setenv(name, value)
        env = agent_env.agent_base_env(str(config))
        assert env["LAB_API_KEY"] == "lab"
        assert (env["PATH"], env["LC_ALL"], env["HTTPS_PROXY"]) == (
            "/usr/bin",
            "C.UTF-8",
            "http://proxy:3128",
        )
        for secret in ("OPENAI_API_KEY", "HF_TOKEN", "GH_TOKEN"):
            assert secret not in env, secret
        # Without a config no key at all reaches the agent.
        assert "LAB_API_KEY" not in agent_env.agent_base_env(None)
