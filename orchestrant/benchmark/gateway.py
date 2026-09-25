#!/usr/bin/env python3
"""Reports taken THROUGH the llm-stack gateway: which lane served, on what.

A lab-* backend's base_url is the gateway (APISIX on 127.0.0.1:9080), not a
lane, and runtime_info() found no process, snapshot or version route behind
that port: a report through it recorded `runtime: null`. The gateway is
rendered from the `serving` block of the registry the lab reads, and that
block names the lane behind every alias. So a gateway report records:

* the route -- the lanes its alias reaches (the primary, then the lane `chat`
  overflows to); runtime_info() answers for the primary exactly as a direct
  run of that lane would, so the two compare like for like;
* /gateway/info -- the running gateway's image and config shas, and whether it
  was rendered from this very registry (`registry_matches`);
* `served` -- the X-Gw-Lane / X-Gw-Rerouted headers of every reply this
  process got from the gateway: the lane that actually answered.

Only a base_url equal to the registry's `serving.gateway.listen` is treated
this way, and only its static /gateway/info route is asked anything: a direct
lane's report is untouched. The gateway itself: ANTfrastructure's
linux/llm-stack/README.md § Gateway.
"""

import collections
import hashlib
import json
import urllib.parse
import urllib.request


# What /gateway/info may put in a report: the image reference and shas.
INFO_KEYS = (
    "image",
    "config_sha256",
    "restart_sha256",
    "registry_sha256",
    "boot_config_sha256",
    "lua_sha256",
    "prompts_sha256",
)

# (origin, alias, lane, rerouted) -> replies. Process-wide on purpose: a report
# is written at the end of a run, from what that run's requests were told.
_SERVED = collections.Counter()


def _origin(url):
    """host:port of `url`, localhost spelled 127.0.0.1; None without both."""
    url = url or ""
    try:
        parts = urllib.parse.urlsplit(url if "://" in url else f"http://{url}")
        port = parts.port
    except ValueError:
        return None
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
    return f"{host}:{port}" if host and port else None


def registry_sha256(doc):
    """The registry's sha256 as the gateway's renderer computes it: canonical JSON."""
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _registry(path=None):
    """(path, document) of the registry the lab resolves backends from."""
    from orchestrant.benchmark import openai_api  # lazy: it imports provenance

    path = path or openai_api.BACKENDS_FILE
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return path, {}
    return path, doc if isinstance(doc, dict) else {}


def _lane_names(serving, alias):
    """[(role, lane)] an alias reaches: a route's lane and overflow, or raw-<lane>."""
    lanes = serving.get("lanes") or {}
    route = (serving.get("routes") or {}).get(alias)
    if isinstance(route, dict):
        pairs = (
            ("primary", route.get("lane")),
            ("overflow", route.get("overflow_lane")),
        )
        return [(role, lane) for role, lane in pairs if lane in lanes]
    raw = alias[4:] if isinstance(alias, str) and alias.startswith("raw-") else None
    offered = serving["gateway"].get("raw_routes") is True
    return [("primary", raw)] if offered and raw in lanes else []


def _lane_rows(serving, backends, alias, gateway):
    """One row per lane an alias reaches, never one pointing back at the gateway."""
    rows = []
    for role, name in _lane_names(serving, alias):
        backend = serving["lanes"][name].get("backend")
        entry = backends.get(backend) or {}
        url = entry.get("base_url")
        url = url.rstrip("/") if isinstance(url, str) else None
        # The renderer refuses such a lane; resolving one would recurse.
        if url and _origin(url) != gateway:
            rows.append(
                {
                    "role": role,
                    "lane": name,
                    "backend": backend,
                    "base_url": url,
                    "model": entry.get("model"),
                }
            )
    return rows


def route(base_url, model, path=None):
    """The lanes a gateway alias reaches, or None when base_url is not the gateway.

    None is the only answer a direct lane gets. For the gateway: the listener,
    the alias, each lane (role, name, backend, base_url, model) and the
    registry read, with its sha. An alias the serving block does not route --
    a typo, or a report over several models -- keeps `lanes` empty and says so
    in `error`.
    """
    path, doc = _registry(path)
    serving = doc.get("serving")
    gw = serving.get("gateway") if isinstance(serving, dict) else None
    listen = gw.get("listen") if isinstance(gw, dict) else None
    gateway = _origin(listen) if isinstance(listen, str) else None
    if gateway is None or _origin(base_url) != gateway:
        return None
    try:
        lanes = _lane_rows(serving, doc.get("backends") or {}, model, gateway)
        error = None if lanes else f"the serving block routes no alias {model!r}"
    except (AttributeError, TypeError, KeyError) as e:  # a hand-edited block
        lanes, error = [], f"unreadable serving block: {type(e).__name__}: {e}"[:200]
    return {
        "listen": listen,
        "alias": model,
        "lanes": lanes,
        "registry": path,
        "registry_sha256": registry_sha256(doc),
        "error": error,
    }


def lane_behind(base_url, model, path=None):
    """(base_url, model) of the lane the gateway sends `model` to first, or None."""
    lanes = (route(base_url, model, path) or {}).get("lanes") or []
    lane = next((r for r in lanes if r["role"] == "primary"), None)
    return (lane["base_url"], lane["model"]) if lane else None


def _header(headers, name):
    try:
        value = headers.get(name) if headers is not None else None
    except Exception:  # a header object of another shape is no evidence
        return None
    return value if isinstance(value, str) else None


def note_reply(url, body, headers):
    """Count one reply by the lane the gateway names in its X-Gw-Lane header.

    client.post_json calls this for every reply, refusals included. A direct
    lane sends no such header, so for it this does nothing.
    """
    lane = _header(headers, "X-Gw-Lane")
    if lane is None:
        return
    alias = body.get("model") if isinstance(body, dict) else None
    alias = alias if isinstance(alias, str) else None
    _SERVED[(_origin(url), alias, lane, _header(headers, "X-Gw-Rerouted"))] += 1


def served(base_url, model=None):
    """Replies this process got from the gateway at base_url, by alias, lane, reroute.

    Every reply counts -- warm-ups and repeat spacers too: the question is
    which lanes answered, not how many cases ran.
    """
    origin = _origin(base_url)
    return [
        {"alias": alias, "lane": lane, "rerouted": rerouted, "replies": n}
        for (where, alias, lane, rerouted), n in sorted(_SERVED.items(), key=str)
        if where == origin and (model is None or alias == model)
    ]


def info(base_url, timeout=3):
    """The running gateway's /gateway/info, cut to INFO_KEYS, or {"error": ...}.

    A static, keyless route: asking it never reaches a lane.
    """
    url = f"{base_url.rstrip('/')}/gateway/info"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # nosec B310
            doc = json.load(r)
    except Exception as e:  # a gateway that is down is a gap, not a crash
        return {"error": f"{type(e).__name__}: {e}"[:160]}
    if not isinstance(doc, dict):
        return {"error": "/gateway/info is not a JSON object"}
    return {k: doc[k] for k in INFO_KEYS if k in doc}


def gateway_block(base_url, model, runtime_of, path=None):
    """provenance["gateway"]: None for a direct lane; for the gateway, what served.

    The route, with `runtime_of(url, model)` for every lane but the primary
    (the primary's is the report's own `runtime`); /gateway/info; whether the
    running gateway was rendered from the registry the route was read from;
    and `served`.
    """
    found = route(base_url, model, path)
    if found is None:
        return None
    for lane in found["lanes"]:
        if lane["role"] != "primary":
            lane["runtime"] = runtime_of(lane["base_url"], lane["model"])
    live = info(base_url)
    rendered_from = live.get("registry_sha256")
    found["info"] = live
    found["registry_matches"] = (
        None if rendered_from is None else rendered_from == found["registry_sha256"]
    )
    found["served"] = served(base_url, model)
    return found


_GATEWAY_SHAS = (
    ("restart_sha256", "image, boot config or Lua"),
    ("config_sha256", "routes"),
)


def _lanes_served(block):
    rows = [r for r in block.get("served") or [] if isinstance(r, dict)]
    return sorted({f"{r.get('lane')} ({r.get('rerouted')})" for r in rows})


def gateway_notes(old, new):
    """For compare(): one run through the gateway and one not, or two gateways apart."""
    before, after = old.get("gateway"), new.get("gateway")
    if not (before or after):
        return []
    if not (before and after):
        side, block = ("old", before) if before else ("new", after)
        return [
            f"only the {side} run went through the gateway (alias "
            f"{block.get('alias')!r}): it re-encodes every body, and an alias "
            f"that is not raw-* also shapes it (tools prompt, T=0 on GGUF lanes, "
            f"power_mode dropped); its /v1/models lists aliases, not models"
        ]
    notes = []
    aliases = before.get("alias"), after.get("alias")
    if aliases[0] != aliases[1]:
        notes.append(f"gateway alias differs: {aliases[0]!r} vs {aliases[1]!r}")
    for key, what in _GATEWAY_SHAS:
        was, now = ((b.get("info") or {}).get(key) for b in (before, after))
        if was and now and was != now:
            notes.append(f"the gateway's {what} changed ({key} {was[:12]}→{now[:12]})")
    lanes = [_lanes_served(b) for b in (before, after)]
    if lanes[0] != lanes[1]:
        notes.append(f"other lanes served the two runs: {lanes[0]} vs {lanes[1]}")
    return notes
