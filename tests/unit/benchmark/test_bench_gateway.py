"""Tests for reports taken through the llm-stack gateway.

A lab-* backend's base_url is the gateway, not a lane: before this, such a
report recorded `runtime: null` and nothing said which lane had answered.
What is pinned here: the lane behind each alias comes from the registry's
serving block, the lane's runtime is recorded exactly as a direct run of it
would be, the X-Gw-Lane header of every reply is counted, and a direct lane's
report does not change at all.
"""

import email.message
import hashlib
import importlib.util
import io
import json
import pathlib
import urllib.error

import pytest

from orchestrant.benchmark import (
    client,
    gateway,
    hostload,
    openai_api,
    provenance as bench_provenance,
)


GW = "http://127.0.0.1:9080"
NPU, GPU, CPU = (
    "http://127.0.0.1:18181",
    "http://127.0.0.1:18182",
    "http://127.0.0.1:18184",
)
NPU_MODEL = "qualcomm/Qwen3-4B-Instruct-2507:W4A16"
GPU_MODEL = "unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0"
CPU_MODEL = "empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M"

# The ONE real function, kept before any test stubs the module attribute.
_REAL_RUNTIME_INFO = bench_provenance.runtime_info
# The registry the lab reads, before the fixture below swaps in its own.
SHIPPED_REGISTRY = pathlib.Path(openai_api.BACKENDS_FILE)


def _registry_doc():
    """The shipped registry's shape, trimmed to what the gateway reads."""
    return {
        "default": "ollama",
        "backends": {
            "geniex-npu": {"base_url": NPU + "/", "model": NPU_MODEL},
            "geniex-gpu": {"base_url": GPU, "model": GPU_MODEL},
            "geniex-cpu-9b": {"base_url": CPU, "model": CPU_MODEL},
            "lab-chat": {"base_url": GW, "model": "chat", "probe": False},
        },
        "serving": {
            "gateway": {"listen": "127.0.0.1:9080", "raw_routes": True},
            "lanes": {
                "npu": {"backend": "geniex-npu"},
                "gpu": {"backend": "geniex-gpu"},
                "cpu": {"backend": "geniex-cpu-9b"},
            },
            "routes": {
                "chat": {"lane": "npu", "overflow_lane": "gpu"},
                "chat-long": {"lane": "gpu"},
                "agent": {"lane": "cpu"},
            },
        },
    }


def _write(tmp_path, doc):
    path = tmp_path / "backends.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    """The registry every lookup here reads; the reply tally starts empty."""
    path = _write(tmp_path, _registry_doc())
    monkeypatch.setattr(openai_api, "BACKENDS_FILE", path)
    monkeypatch.setattr(gateway, "_SERVED", gateway.collections.Counter())
    return path


def _info_answers(monkeypatch, body):
    """Stand /gateway/info in with `body`; record the URLs asked."""
    asked = []

    def fake_urlopen(url, timeout=None):
        asked.append(url)
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    return asked


class TestRoute:
    def test_a_direct_lane_is_not_the_gateway(self):
        assert gateway.route(NPU, NPU_MODEL) is None
        assert gateway.route("http://summy-server:9080", "chat") is None
        assert gateway.lane_behind(NPU, NPU_MODEL) is None

    def test_an_alias_reaches_its_lane_then_its_overflow_lane(self, registry):
        found = gateway.route(GW, "chat")
        assert [(r["role"], r["lane"]) for r in found["lanes"]] == [
            ("primary", "npu"),
            ("overflow", "gpu"),
        ]
        # base_url and model are the lane's backends entry: what the gateway sends.
        assert found["lanes"][0]["base_url"] == NPU
        assert found["lanes"][0]["model"] == NPU_MODEL
        assert found["lanes"][1]["backend"] == "geniex-gpu"
        assert found["registry"] == registry
        assert found["error"] is None

    def test_localhost_and_a_trailing_slash_are_the_same_gateway(self):
        assert gateway.lane_behind("http://localhost:9080/", "agent") == (
            CPU,
            CPU_MODEL,
        )

    def test_a_raw_route_reaches_only_its_lane(self):
        assert gateway.lane_behind(GW, "raw-gpu") == (GPU, GPU_MODEL)
        assert [r["role"] for r in gateway.route(GW, "raw-gpu")["lanes"]] == ["primary"]

    def test_raw_routes_switched_off_route_nothing(self, tmp_path, monkeypatch):
        doc = _registry_doc()
        doc["serving"]["gateway"]["raw_routes"] = False
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", _write(tmp_path, doc))
        found = gateway.route(GW, "raw-npu")
        assert found["lanes"] == [] and "raw-npu" in found["error"]

    def test_an_unrouted_alias_is_named_not_guessed(self):
        # A report over several models passes no single model at all.
        for alias in ("chta", None):
            found = gateway.route(GW, alias)
            assert found["lanes"] == [] and repr(alias) in found["error"]

    def test_a_lane_pointing_back_at_the_gateway_is_dropped(
        self, tmp_path, monkeypatch
    ):
        doc = _registry_doc()
        doc["serving"]["lanes"]["gpu"]["backend"] = "lab-chat"
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", _write(tmp_path, doc))
        assert [r["lane"] for r in gateway.route(GW, "chat")["lanes"]] == ["npu"]
        assert gateway.lane_behind(GW, "chat-long") is None

    def test_without_its_primary_an_alias_names_no_lane_behind_it(
        self, tmp_path, monkeypatch
    ):
        # The overflow lane is not what served first: better no runtime than
        # the wrong lane's.
        doc = _registry_doc()
        doc["serving"]["lanes"]["npu"]["backend"] = "lab-chat"
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", _write(tmp_path, doc))
        assert [r["role"] for r in gateway.route(GW, "chat")["lanes"]] == ["overflow"]
        assert gateway.lane_behind(GW, "chat") is None

    def test_a_malformed_serving_block_is_an_error_not_a_crash(
        self, tmp_path, monkeypatch
    ):
        doc = _registry_doc()
        doc["serving"]["lanes"] = ["npu"]
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", _write(tmp_path, doc))
        found = gateway.route(GW, "chat")
        assert found["lanes"] == [] and "unreadable serving block" in found["error"]

    def test_a_registry_without_a_serving_block_has_no_gateway(
        self, tmp_path, monkeypatch
    ):
        doc = _registry_doc()
        del doc["serving"]
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", _write(tmp_path, doc))
        assert gateway.route(GW, "chat") is None

    def test_the_registry_sha_is_the_one_the_renderer_puts_in_gateway_info(
        self, registry
    ):
        # /gateway/info's registry_sha256 is render_apisix.py's canonical sha;
        # registry_matches is only evidence while the two are computed alike.
        module = _renderer()
        doc = _registry_doc()
        want = module.sha256_hex(module.canonical(doc).encode("ascii"))
        assert gateway.route(GW, "chat")["registry_sha256"] == want


def _hub_file(*parts):
    """A gateway file beside the shipped registry, or a skip without the hub."""
    path = SHIPPED_REGISTRY.with_name("gateway").joinpath(*parts)
    if not path.is_file():
        pytest.skip(f"no gateway beside the registry ({path})")
    return path


def _renderer():
    spec = importlib.util.spec_from_file_location(
        "render_apisix", _hub_file("render_apisix.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAgreesWithTheHub:
    """gateway.py reads what the hub's gateway emits at THIS pin.

    The headers, the /gateway/info keys and the lane behind each alias cross
    the gitlink; a hub change to any of them would empty `served`, drop a sha
    or name the wrong lane's runtime without a single test here going red.
    """

    @pytest.fixture(autouse=True)
    def _shipped(self, monkeypatch):
        _hub_file("render_apisix.py")
        monkeypatch.setattr(openai_api, "BACKENDS_FILE", str(SHIPPED_REGISTRY))

    def test_every_alias_reaches_the_lanes_the_renderer_routes(self):
        doc = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
        serving = _renderer().parse_serving(doc)
        lanes, gw = serving["lanes"], f"http://{doc['serving']['gateway']['listen']}"
        routes = serving["routes"]
        routed = {r["alias"]: [r["lane"], r["overflow_lane"]] for r in routes}
        if serving["gateway"]["raw_routes"]:
            routed.update({f"raw-{name}": [name, None] for name in lanes})
        for alias, pair in routed.items():
            found = gateway.route(gw, alias)
            assert [r["lane"] for r in found["lanes"]] == [n for n in pair if n]
            lane = lanes[pair[0]]
            assert gateway.lane_behind(gw, alias) == (lane["endpoint"], lane["model"])

    def test_every_registry_entry_at_the_gateway_names_a_lane(self):
        backends = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))["backends"]
        at_gateway = {
            name: gateway.lane_behind(e.get("base_url"), e.get("model"))
            for name, e in backends.items()
            if gateway.route(e.get("base_url"), e.get("model")) is not None
        }
        assert at_gateway, "no registry entry reaches the gateway"
        assert [name for name, lane in at_gateway.items() if lane is None] == []

    def test_gateway_info_carries_every_key_a_report_keeps(self):
        module = _renderer()
        prompts = pathlib.Path(__file__).resolve().parents[3] / "benchmarks" / "prompts"
        _, meta = module.render(
            str(SHIPPED_REGISTRY), str(prompts), module.DEFAULT_OVERLAY
        )
        assert set(gateway.INFO_KEYS) <= set(meta)

    def test_the_plugin_sets_the_headers_note_reply_counts(self):
        lua = _hub_file("lua", "apisix", "plugins", "geniex-shape.lua")
        text = lua.read_text(encoding="utf-8")
        for header in ("X-Gw-Lane", "X-Gw-Rerouted"):
            assert f'set_header("{header}"' in text


def _headers(**fields):
    msg = email.message.Message()
    for name, value in fields.items():
        msg[name.replace("_", "-")] = value
    return msg


class TestServed:
    def test_a_reply_is_counted_by_the_lane_it_names(self):
        gateway.note_reply(
            f"{GW}/v1/chat/completions",
            {"model": "chat"},
            _headers(X_Gw_Lane="gpu", X_Gw_Rerouted="overflow"),
        )
        gateway.note_reply(
            f"{GW}/v1/chat/completions", {"model": "chat"}, _headers(X_Gw_Lane="npu")
        )
        assert gateway.served(GW, "chat") == [
            {"alias": "chat", "lane": "gpu", "rerouted": "overflow", "replies": 1},
            {"alias": "chat", "lane": "npu", "rerouted": None, "replies": 1},
        ]
        assert gateway.served(GW, "agent") == []
        assert gateway.served(NPU) == []

    def test_a_direct_lane_reply_counts_nothing(self):
        gateway.note_reply(f"{NPU}/v1/chat/completions", {"model": "m"}, _headers())
        gateway.note_reply(f"{NPU}/v1/chat/completions", {"model": "m"}, {})
        gateway.note_reply(f"{NPU}/v1/chat/completions", {"model": "m"}, None)
        assert gateway.served(NPU) == []

    def test_a_header_that_is_not_a_string_is_no_evidence(self):
        class Odd:
            def get(self, name):
                return object()

        gateway.note_reply(f"{GW}/v1/chat/completions", {"model": "chat"}, Odd())
        assert gateway.served(GW) == []


class FakeResponse(io.BytesIO):
    def __init__(self, headers):
        super().__init__(b"{}")
        self.status = 200
        self.headers = headers


class TestThroughPostJson:
    """client.post_json is every tool's request path; it does the counting."""

    def test_a_gateway_reply_is_counted(self, monkeypatch):
        reply = FakeResponse(_headers(X_Gw_Lane="npu", X_Gw_Rerouted="no"))
        monkeypatch.setattr(client.urllib.request, "urlopen", lambda r, timeout: reply)
        with client.post_json(f"{GW}/v1/chat/completions", {"model": "chat"}) as r:
            assert r.json() == {}
        assert gateway.served(GW, "chat")[0]["lane"] == "npu"

    def test_a_refusal_is_counted_and_still_raised(self, monkeypatch):
        refusal = urllib.error.HTTPError(
            f"{GW}/v1/chat/completions",
            503,
            "unavailable",
            _headers(X_Gw_Lane="gpu", X_Gw_Rerouted="overflow"),
            io.BytesIO(b"{}"),
        )

        def refuse(req, timeout):
            raise refusal

        monkeypatch.setattr(client.urllib.request, "urlopen", refuse)
        # Closed, not left to the collector: an HTTPError is a temp-file wrapper,
        # and one collected late warns (ResourceWarning) after the session has
        # passed -- filterwarnings=error turned that into exit 1.
        with refusal, pytest.raises(urllib.error.HTTPError) as e:
            client.post_json(f"{GW}/v1/chat/completions", {"model": "chat"})
        assert e.value is refusal
        assert gateway.served(GW, "chat")[0]["rerouted"] == "overflow"

    def test_a_direct_reply_leaves_no_trace(self, monkeypatch):
        reply = FakeResponse(_headers())
        monkeypatch.setattr(client.urllib.request, "urlopen", lambda r, timeout: reply)
        client.post_json(f"{NPU}/v1/chat/completions", {"model": NPU_MODEL}).close()
        assert gateway.served(NPU) == []


class TestInfo:
    def test_only_the_known_shas_are_kept(self, monkeypatch):
        asked = _info_answers(
            monkeypatch, {"config_sha256": "c" * 64, "image": "x@sha256:1", "k": "v"}
        )
        assert gateway.info(GW + "/") == {
            "config_sha256": "c" * 64,
            "image": "x@sha256:1",
        }
        assert asked == [f"{GW}/gateway/info"]

    def test_a_gateway_that_is_down_is_a_recorded_gap(self, monkeypatch):
        _info_answers(monkeypatch, OSError("refused"))
        assert "refused" in gateway.info(GW)["error"]

    def test_a_body_that_is_not_an_object_is_a_recorded_gap(self, monkeypatch):
        _info_answers(monkeypatch, ["no"])
        assert "not a JSON object" in gateway.info(GW)["error"]


class TestGatewayBlock:
    def test_a_direct_lane_gets_none_and_asks_nothing(self, monkeypatch):
        asked = _info_answers(monkeypatch, {})
        assert gateway.gateway_block(NPU, NPU_MODEL, lambda u, m: 1 / 0) is None
        assert asked == []

    def test_the_gateway_block_names_the_lanes_and_what_served(self, monkeypatch):
        sha = gateway.route(GW, "chat")["registry_sha256"]
        _info_answers(monkeypatch, {"registry_sha256": sha, "config_sha256": "c"})
        gateway.note_reply(GW, {"model": "chat"}, _headers(X_Gw_Lane="npu"))
        seen = []

        def runtime_of(url, model):
            seen.append((url, model))
            return {"server": "geniex", "cli": "v0.7.0"}

        block = gateway.gateway_block(GW, "chat", runtime_of)
        # The primary's runtime is the report's own; only the overflow lane's is here.
        assert seen == [(GPU, GPU_MODEL)]
        assert "runtime" not in block["lanes"][0]
        assert block["lanes"][1]["runtime"]["cli"] == "v0.7.0"
        assert block["registry_matches"] is True
        assert block["info"]["config_sha256"] == "c"
        assert block["served"][0]["lane"] == "npu"

    def test_a_gateway_rendered_from_another_registry_says_so(self, monkeypatch):
        _info_answers(monkeypatch, {"registry_sha256": "0" * 64})
        assert gateway.gateway_block(GW, "agent", None)["registry_matches"] is False

    def test_an_unreachable_gateway_leaves_the_match_unknown(self, monkeypatch):
        _info_answers(monkeypatch, OSError("down"))
        block = gateway.gateway_block(GW, "agent", None)
        assert block["registry_matches"] is None and "down" in block["info"]["error"]


class _NoLane:
    """hostload.LaneProcess on a host where no lane process is visible."""

    available = False
    reason = "stub"
    seen = []

    def __init__(self, url):
        _NoLane.seen.append(url)


class TestRuntimeInfo:
    @pytest.fixture(autouse=True)
    def _no_probes(self, monkeypatch):
        _NoLane.seen = []
        monkeypatch.setattr(hostload, "LaneProcess", _NoLane)
        monkeypatch.setattr(bench_provenance, "_ollama_version", lambda *a, **k: None)

    def test_a_gateway_alias_answers_with_its_lanes_runtime(self, monkeypatch):
        snapshots = []

        def snapshot(url, model=None):
            snapshots.append((url, model))
            return {"server": "geniex", "cli": "v0.7.0", "verified": True}

        monkeypatch.setattr(bench_provenance, "load_lane_runtime", snapshot)
        runtime = _REAL_RUNTIME_INFO(GW, "chat")
        # The lane process, the snapshot and the model files are the NPU's.
        assert _NoLane.seen == [NPU]
        assert snapshots == [(NPU, NPU_MODEL)]
        assert runtime["cli"] == "v0.7.0"

    def test_a_direct_lane_is_looked_up_as_before(self, monkeypatch):
        snapshots = []
        monkeypatch.setattr(
            bench_provenance,
            "load_lane_runtime",
            lambda url, model=None: snapshots.append((url, model)),
        )
        monkeypatch.setattr(bench_provenance, "_server_models", lambda *a, **k: None)
        assert _REAL_RUNTIME_INFO(GPU, "some/model:Q4_0") is None
        assert _NoLane.seen == [GPU]
        assert snapshots == [(GPU, "some/model:Q4_0")]


class TestCollect:
    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        monkeypatch.setattr(bench_provenance, "busy_lanes", lambda *a, **k: [])
        monkeypatch.setattr(bench_provenance, "_server_models", lambda *a, **k: None)
        monkeypatch.setattr(
            bench_provenance, "runtime_info", lambda url, model=None: {"url": url}
        )

    def test_a_gateway_report_carries_the_gateway_block(self, monkeypatch):
        _info_answers(monkeypatch, {"config_sha256": "c"})
        prov = bench_provenance.collect(base_url=GW, model="chat")
        assert prov["gateway"]["alias"] == "chat"
        assert prov["gateway"]["lanes"][1]["runtime"] == {"url": GPU}

    def test_a_direct_report_carries_a_null_gateway(self, monkeypatch):
        asked = _info_answers(monkeypatch, {})
        prov = bench_provenance.collect(base_url=NPU, model=NPU_MODEL)
        assert prov["gateway"] is None
        assert asked == []


def _prov(block):
    return {"gateway": block}


def _block(alias="chat", config="c", restart="r", lanes=(("npu", "no"),)):
    return {
        "alias": alias,
        "info": {"config_sha256": config, "restart_sha256": restart},
        "served": [
            {"lane": lane, "rerouted": why, "replies": 1} for lane, why in lanes
        ],
    }


class TestCompare:
    def test_two_direct_runs_say_nothing(self):
        assert gateway.gateway_notes({}, {"gateway": None}) == []

    def test_the_same_gateway_twice_says_nothing(self):
        assert gateway.gateway_notes(_prov(_block()), _prov(_block())) == []

    def test_a_gateway_run_against_a_direct_one_is_named(self):
        (note,) = gateway.gateway_notes(_prov(_block(alias="raw-npu")), {})
        assert "only the old run went through the gateway" in note
        assert "'raw-npu'" in note

    def test_a_changed_gateway_is_named(self):
        old = _prov(_block())
        new = _prov(_block(alias="chat-long", config="d", restart="s"))
        notes = gateway.gateway_notes(old, new)
        assert any("alias differs" in n for n in notes)
        assert any("image, boot config or Lua changed" in n for n in notes)
        assert any("routes changed" in n for n in notes)

    def test_a_run_that_overflowed_is_told_from_one_that_did_not(self):
        new = _prov(_block(lanes=(("npu", "no"), ("gpu", "overflow"))))
        (note,) = gateway.gateway_notes(_prov(_block()), new)
        assert "gpu (overflow)" in note

    def test_compare_carries_the_gateway_notes(self):
        notes = bench_provenance.compare(_prov(_block()), {"gateway": None})
        assert any("through the gateway" in n for n in notes)

    def test_a_gateway_run_against_a_direct_one_blames_no_pull(self):
        # The gateway's /v1/models is its alias list and a GenieX lane's is its
        # cache: they never match, and "a pull alone changes it" was a false
        # lead in every Stage A comparison.
        old = {"gateway": None, "server_models": [NPU_MODEL]}
        new = {**_prov(_block(alias="raw-npu")), "server_models": ["agent", "chat"]}
        notes = bench_provenance.compare(old, new)
        assert not any("served models differ" in n for n in notes)
        (note,) = [n for n in notes if "through the gateway" in n]
        assert "/v1/models" in note

    def test_two_gateway_runs_still_compare_their_model_lists(self):
        old = {**_prov(_block()), "server_models": ["chat"]}
        new = {**_prov(_block()), "server_models": ["agent", "chat"]}
        notes = bench_provenance.compare(old, new)
        assert any("served models differ" in n for n in notes)


def test_registry_sha256_is_canonical_json():
    doc = {"b": [1, 2.5], "a": "é"}
    text = '{"a":"\\u00e9","b":[1,2.5]}'
    assert gateway.registry_sha256(doc) == hashlib.sha256(text.encode()).hexdigest()
