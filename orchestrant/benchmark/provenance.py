#!/usr/bin/env python3
"""Record WHAT produced a measurement, so two reports can be compared later.

A benchmark result without provenance is a number without a claim. Four weeks
on you cannot say which model build, which runtime, or which repository state
produced it -- which makes run-to-run regression comparison impossible, and
makes an old number worse than no number because it looks authoritative.

Every field that cannot be determined is recorded as an explicit `null` and
listed in `incomplete`, rather than being silently omitted: a gap you can see
is a gap you can fix.
"""

import glob
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime


SCHEMA_VERSION = 1


def _git(*args):
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _server_models(base_url, timeout=5):
    """Model ids the endpoint advertises.

    Not what is LOADED: GenieX lists its whole local model cache here and
    Ollama every pulled tag, so this changes whenever a model is pulled and
    says nothing about which one produced a number — the report's own config
    names that.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=timeout) as r:
            return sorted(m["id"] for m in json.load(r).get("data", []))
    except Exception:
        return None


_GENIEX_VERSION_KEYS = {
    "geniex cli version": "cli",
    "qairt runtime version": "qairt",
    "llamacpp runtime hash": "llama_cpp",
}


def parse_geniex_version(text):
    """`geniex --version` -> {"cli", "qairt", "llama_cpp"}; absent lines stay absent.

    All three lines matter: the llama.cpp hash decides GGUF behaviour (the
    i-quant kernels, the output cap) independently of the CLI version.
    """
    out = {}
    for line in (text or "").splitlines():
        name, sep, value = line.partition(":")
        key = _GENIEX_VERSION_KEYS.get(name.strip().lower())
        if sep and key and value.strip():
            out[key] = value.strip()
    return out


def _installed_geniex():
    """The GenieX CLI a lane on this machine would run, or None.

    A WSL2 client looks on the Windows side first: that is where the documented
    topology runs the lanes, and a Linux-side `geniex` is a different install.
    """
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "GenieX CLI", "geniex.exe"))
    candidates += sorted(
        glob.glob("/mnt/c/Users/*/AppData/Local/GenieX CLI/geniex.exe")
    )
    found = shutil.which("geniex")
    if found:
        candidates.append(found)
    return next((c for c in candidates if os.path.isfile(c)), None)


def _geniex_version(exe, timeout=30):
    try:
        out = subprocess.run(
            [exe, "--version", "--skip-update"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    return parse_geniex_version(out.stdout + out.stderr) or None


def _ollama_version(base_url, timeout=3):
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=timeout) as r:  # nosec B310
            return json.load(r).get("version")
    except Exception:
        return None


def _serves_geniex_root(base_url, timeout=3):
    """Does the root page look like GenieX's Swagger UI?

    GenieX exposes no version route; that page is its only HTTP signature
    (checked on v0.6.1). Weak alone — llama.cpp and vLLM serve other things
    there — which is why it only gates the installed-binary fallback and never
    produces `verified: True`.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=timeout) as r:  # nosec B310
            return b"swagger-ui" in r.read(4096)
    except Exception:
        return False


# ── what served: the model files, the drivers, the host's own view ──────────
# The runtime block named the server build and nothing under or beside it: not
# the weights a model id resolved to, not the bundle config that decides how
# they sample, not the drivers, and in a multi-lane report no lane but the one
# the provenance block was collected for.

# <cache>/<org>/<repo>/geniex.json maps each variant of a model id to its file
# (third_party/ANTfrastructure/docs/geniex-local-ai-setup.md § Model management).
_GENIEX_CACHE = (".cache", "geniex", "models")

# GGUFs here are 1.6-16 GB: hashing one whole on every report is minutes of
# disk. The first MiB is no quant's identity: it is general.* and the start of
# the vocabulary, byte-identical in this cache's four Qwen3-4B quants (Q2_K to
# Q4_0; general.file_type sits at 5.66 MiB) and its three 27B UD quants, whose
# last MiB matches too. So weights are sampled as well: 16 windows of 64 KiB
# spread evenly to the end tell every model file in that cache apart, ~10 ms
# each. Still not a content hash: an edit between the windows is unseen.
_HEAD_BYTES = 1 << 20
_SAMPLES, _SAMPLE_BYTES = 16, 1 << 16


def _sampled_sha256(f, size):
    """sha256 over _SAMPLES windows at offsets i * (size - window) // (n - 1)."""
    h = hashlib.sha256()
    span = max(size - _SAMPLE_BYTES, 0)
    for i in range(_SAMPLES):
        f.seek(span * i // (_SAMPLES - 1))
        h.update(f.read(_SAMPLE_BYTES))
    return h.hexdigest()


def _file_identity(path, manifest_size=None, whole=False):
    """Name, size, mtime and a sha256 -- whole, or of the first MiB and a sample.

    `size_matches_manifest` compares with the size geniex.json recorded at
    download, a cheap sign that a file was replaced or edited in place.
    """
    ident = {"name": os.path.basename(path)}
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            digest = hashlib.sha256(f.read(None if whole else _HEAD_BYTES)).hexdigest()
            sampled = None if whole else _sampled_sha256(f, st.st_size)
    except OSError as e:
        return {**ident, "error": f"{type(e).__name__}: {e}"[:160]}
    ident["size"] = st.st_size
    ident["modified_utc"] = datetime.fromtimestamp(st.st_mtime, UTC).isoformat(
        timespec="seconds"
    )
    if whole:
        ident["sha256"] = digest
    else:
        ident.update(
            head_sha256=digest,
            head_bytes=_HEAD_BYTES,
            sampled_sha256=sampled,
            sampled=f"{_SAMPLES} x {_SAMPLE_BYTES} bytes, evenly to the end",
        )
    ident["size_matches_manifest"] = (
        None if manifest_size is None else st.st_size == manifest_size
    )
    return ident


def _read_json(path):
    """A JSON object from `path`, or None: a missing or broken file is a gap."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _geniex_cache_roots(local_only=False):
    """The model caches to look in: this OS's, and from WSL2 the Windows host's first.

    The same order as _installed_geniex(): the documented topology serves the
    lanes from Windows, and a Linux-side cache belongs to a different install.
    """
    local = str(pathlib.Path.home().joinpath(*_GENIEX_CACHE))
    if local_only or sys.platform == "win32":
        return [local]
    windows = sorted(glob.glob("/mnt/c/Users/*/" + "/".join(_GENIEX_CACHE)))
    return [*windows, local]


def _find_manifest(roots, org, repo):
    """(folder, geniex.json) for org/repo in the first root that has it, or None."""
    for root in roots:
        folder = os.path.join(root, org, repo)
        manifest = _read_json(os.path.join(folder, "geniex.json"))
        if manifest is not None:
            return folder, manifest
    return None


def _manifest_sizes(manifest):
    """{file name: the size geniex.json recorded when it was downloaded}."""
    entries = [
        *(manifest.get("ModelFile") or {}).values(),
        manifest.get("MMProjFile") or {},
        *(manifest.get("ExtraFiles") or []),
    ]
    return {
        e["Name"]: e.get("Size")
        for e in entries
        if isinstance(e, dict) and e.get("Name")
    }


def _pick_variant(variants, variant):
    """The ModelFile key a model id names: exactly, ignoring case, or the only one."""
    if variant is None:
        return next(iter(variants)) if len(variants) == 1 else None
    if variant in variants:
        return variant
    return next((k for k in variants if k.lower() == variant.lower()), None)


def _qairt_bundle(folder, sizes):
    """A QAIRT bundle's genie_config.json, the context binaries it loads, and more.

    genie_config.json is hashed whole and its sampler and context lifted out:
    the sampler is what answers `temperature: 0` on this lane (temp 0.8, top-k
    40, seed 42 -- the v0.7.0 page's T=0 finding), the context is the
    hard-compiled 4096, and this host's cache holds a
    genie_config.json.orig-backup beside one bundle's, so they are edited in
    place. The extensions file pins the HTP perf profile; metadata.json names
    the QAIRT the bundle was compiled with, which need not be the runtime's.
    """
    path = os.path.join(folder, "genie_config.json")
    dialog = (_read_json(path) or {}).get("dialog") or {}
    engine = dialog.get("engine") or {}
    bins = ((engine.get("model") or {}).get("binary") or {}).get("ctx-bins") or []
    ext = (engine.get("backend") or {}).get("extensions")
    meta = _read_json(os.path.join(folder, "metadata.json")) or {}
    return {
        "files": [_file_identity(os.path.join(folder, b), sizes.get(b)) for b in bins],
        "genie_config": {
            **_file_identity(path, sizes.get("genie_config.json"), whole=True),
            "sampler": dialog.get("sampler"),
            "context_size": (dialog.get("context") or {}).get("size"),
        },
        "backend_extensions": _file_identity(
            os.path.join(folder, ext), sizes.get(ext), whole=True
        )
        if ext
        else None,
        "bundle_qairt": (meta.get("tool_versions") or {}).get("qairt"),
        "precision": meta.get("precision"),
        # No weights named is a gap to say, not an empty list to trust.
        "error": None if bins else "genie_config.json names no ctx-bins",
    }


def _model_files(model, roots):
    repo_id, _, variant = model.partition(":")
    org, _, repo = repo_id.partition("/")
    out = {
        "model": model,
        "cache_dir": None,
        "plugin": None,
        "variant": None,
        "files": [],
        "error": None,
    }
    found = _find_manifest(roots, org, repo) if org and repo else None
    if found is None:
        return {**out, "error": f"no geniex.json for {repo_id!r} in {roots}"}
    folder, manifest = found
    variants = manifest.get("ModelFile") or {}
    key = _pick_variant(variants, variant or None)
    out.update(cache_dir=folder, plugin=manifest.get("PluginId"), variant=key)
    if key is None:
        why = f"variant {variant!r} is not" if variant else "the id names no variant of"
        return {**out, "error": f"{why} the manifest's {sorted(variants)}"}
    sizes = _manifest_sizes(manifest)
    if out["plugin"] == "qairt":
        return {**out, **_qairt_bundle(folder, sizes)}
    names = [variants[key].get("Name"), (manifest.get("MMProjFile") or {}).get("Name")]
    out["files"] = [
        _file_identity(os.path.join(folder, n), sizes.get(n)) for n in names if n
    ]
    return out


def geniex_model_files(model, roots=None):
    """WHICH files a GenieX model id resolves to in the lane's cache, and their identity.

    `model` is the id the report served; the caller knows it, the server does
    not say (/v1/models lists the whole cache). A GGUF is recorded by size,
    mtime and a hash of its first MiB (and its mmproj, for a VLM); a QAIRT
    bundle by its genie_config.json, its context binaries and metadata.

    Read-only, and never raises: a report must be written regardless, and a
    gap it names beats a crash.
    """
    if not model:
        return None
    try:
        return _model_files(model, roots or _geniex_cache_roots())
    except Exception as e:  # a manifest in a shape this does not know
        return {"model": model, "error": f"{type(e).__name__}: {e}"[:200]}


# Windows device setup classes. The PnP manager keeps each installed driver's
# version under Control\Class\<guid>\NNNN, readable without admin rights or a
# subprocess (a Get-CimInstance Win32_PnPSignedDriver query took 2.4 s here).
_DRIVER_CLASSES = {
    "npu": "{f01a9d53-3ff6-48d2-9f97-c8a7004be10c}",  # ComputeAccelerator: Hexagon
    "gpu": "{4d36e968-e325-11ce-bfc1-08002be10318}",  # Display: Adreno
}
_DRIVER_FIELDS = {
    "DriverDesc": "name",
    "DriverVersion": "version",
    "DriverDate": "date",
    "ProviderName": "provider",
    "InfPath": "inf",
}


def _driver_row(winreg, parent, sub):
    """One installed driver's name, version, date, provider and INF, or None."""
    row = {}
    try:
        with winreg.OpenKey(parent, sub) as key:
            for value, field in _DRIVER_FIELDS.items():
                try:
                    row[field] = str(winreg.QueryValueEx(key, value)[0])
                except OSError:  # a value this driver's INF does not set
                    row[field] = None
    except OSError:  # a device key this user may not read
        return None
    return row


def _class_drivers(winreg, guid):
    """Every non-Microsoft driver installed in one device setup class."""
    base = rf"SYSTEM\CurrentControlSet\Control\Class\{guid}"
    rows = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
        for i in range(winreg.QueryInfoKey(cls)[0]):
            sub = winreg.EnumKey(cls, i)
            row = _driver_row(winreg, cls, sub) if sub.isdigit() else None
            # Microsoft's are the remote and basic display adapters, never a lane's.
            if row and row["version"] and row["provider"] != "Microsoft":
                rows.append(row)
    return rows


def driver_versions():
    """The NPU and GPU driver versions under a Windows lane, or None with the reason.

    A QAIRT bundle runs on the Hexagon NPU driver and a GPU lane on the Adreno
    one, and Windows Update moves both while the GenieX build stays put: a
    report naming only the build cannot tell a driver update from a regression.
    """
    out = {"npu": None, "gpu": None, "source": None, "reason": None}
    if sys.platform != "win32":
        out["reason"] = (
            f"not a Windows host ({sys.platform}); from WSL2 the host's drivers "
            f"are invisible, and a lane-runtime file written on the host "
            f"({LANE_RUNTIMES_ENV}) carries them"
        )
        return out
    import winreg

    out["source"] = r"HKLM\SYSTEM\CurrentControlSet\Control\Class"
    for kind, guid in _DRIVER_CLASSES.items():
        try:
            out[kind] = _class_drivers(winreg, guid)
        except OSError as e:
            out["reason"] = f"{kind}: {type(e).__name__}: {e}"[:160]
    return out


def _geniex_serving(model, roots=None):
    """What a GenieX runtime adds: the served model's files and the drivers."""
    return {
        "model_files": geniex_model_files(model, roots),
        "drivers": driver_versions(),
    }


# From WSL2 the Windows-side lane process is invisible, so runtime_info() could
# only guess from the installed binary. A lane-runtime file is the host's own
# view, written on Windows (`python -m orchestrant.benchmark.provenance`) and
# named by this variable in WSL2.
LANE_RUNTIMES_ENV = "LLM_LANE_RUNTIMES"

# A snapshot describes the process that listened when it was taken, and a
# restart since is invisible from WSL2. The lanes are restarted for each
# measurement round (Start-GeniexServers.ps1 -Restart), so past this age -- a
# judgement: one working session -- it keeps its data but loses `verified`.
SNAPSHOT_MAX_AGE_S = 12 * 3600


def lane_runtimes(lanes):
    """{name: runtime_info(url, model)} for {name: (url, model)}; never raises."""
    out = {}
    for name, (url, model) in lanes.items():
        try:
            out[name] = runtime_info(url, model)
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out


def write_lane_runtimes(path, lanes):
    """Snapshot what serves each lane, on the host that can see the lane processes.

    Run it on Windows once the lanes are up; export LLM_LANE_RUNTIMES=<the
    file's /mnt/c path> in WSL2, and every report there takes each lane's
    runtime from it instead of guessing from the installed binary.
    """
    runtimes = lane_runtimes(lanes)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": "lane_runtimes",
        "captured_utc": datetime.now(UTC).isoformat(),
        "host": platform.node() or None,
        "lanes": [
            {"lane": name, "base_url": url, "model": model, "runtime": runtimes[name]}
            for name, (url, model) in lanes.items()
        ],
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)
    return doc


def _names_a_server(entry):
    """A snapshot entry the host attributed: {"error": ...} or null is no evidence."""
    runtime = entry.get("runtime") if isinstance(entry, dict) else None
    return isinstance(runtime, dict) and bool(runtime.get("server"))


def _snapshot_entry(entries, base_url):
    """The entry for `base_url`: exactly, else the only loopback one on its port.

    WSL2 reaches a mirrored-network lane as localhost or 127.0.0.1 alike; a
    remote URL never matches by port, which would name another host's lane.
    An entry naming no server is skipped, so the probes after the snapshot
    still run for a lane the host could not attribute.
    """
    from orchestrant.benchmark.hostload import _port

    def port(u):
        try:
            return _port(u)
        except ValueError:  # a malformed port in a hand-edited file
            return None

    url = (base_url or "").rstrip("/")
    entries = [e for e in entries if _names_a_server(e)]
    exact = [e for e in entries if (e.get("base_url") or "").rstrip("/") == url]
    by_port = [e for e in entries if port(url) and port(e.get("base_url")) == port(url)]
    return exact[0] if exact else (by_port[0] if len(by_port) == 1 else None)


def _age_s(stamp):
    try:
        return round(
            (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()
        )
    except (TypeError, ValueError):
        return None


def _installed_cli_mismatch(runtime):
    """The installed GenieX's version when it is not the snapshot lane's, else None.

    GenieX v0.6.1 -> v0.7.0 was one session on 2026-09-23: a snapshot taken
    before the upgrade is young enough to keep `verified` while the restarted
    lanes run the new build. The installed binary is the one those lanes
    start from, and WSL2 reaches it through /mnt/c.
    """
    if runtime.get("server") != "geniex":
        return None
    installed = _installed_geniex()
    cli = (_geniex_version(installed) or {}).get("cli") if installed else None
    return cli if cli and cli != runtime.get("cli") else None


def load_lane_runtime(base_url, path=None, model=None, max_age_s=SNAPSHOT_MAX_AGE_S):
    """The lane-runtime file's runtime for `base_url`, or None.

    `path` defaults to $LLM_LANE_RUNTIMES. The entry keeps the host's own
    `verified` only while the snapshot is younger than `max_age_s` and the
    installed GenieX is still the build it names, and records the file, its
    age and any such mismatch in `source` and `snapshot`. When `model` is not
    the one the snapshot resolved, a GenieX entry's model files are looked up
    again here -- from WSL2, in the Windows cache through /mnt/c.
    """
    path = path or os.environ.get(LANE_RUNTIMES_ENV)
    doc = (_read_json(path) if path else None) or {}
    entry = _snapshot_entry(doc.get("lanes") or [], base_url) or {}
    runtime = dict(entry.get("runtime") or {})
    if not runtime:
        return None
    captured, age = doc.get("captured_utc"), _age_s(doc.get("captured_utc"))
    fresh = age is not None and age <= max_age_s
    mismatch = _installed_cli_mismatch(runtime)
    runtime["verified"] = bool(runtime.get("verified")) and fresh and not mismatch
    runtime["source"] = f"lane-runtime file {path}: {runtime.get('source')}"
    runtime["snapshot"] = {
        "path": path,
        "lane": entry.get("lane"),
        "host": doc.get("host"),
        "captured_utc": captured,
        "age_s": age,
        "stale": not fresh,
        "installed_cli_mismatch": mismatch,
    }
    return _with_model_files(runtime, model)


def _with_model_files(runtime, model):
    """`runtime`, its GenieX model files resolved again when another id served."""
    served = (runtime.get("model_files") or {}).get("model")
    if model and runtime.get("server") == "geniex" and served != model:
        runtime["model_files"] = geniex_model_files(model)
    return runtime


def _file_keys(model_files):
    """({name: (size, head, sample)} of the files read, {every name listed}).

    mtime is left out: a copy or a restore moves it and nothing else.
    """
    files = [f for f in model_files.get("files") or [] if isinstance(f, dict)]
    read = {
        f.get("name"): (f.get("size"), f.get("head_sha256"), f.get("sampled_sha256"))
        for f in files
        if f.get("size") is not None
    }
    return read, {f.get("name") for f in files}


def _changed_files(old, new):
    """Names listed on one side only, or read on both with another identity.

    A file one side could not read (a lane holding it, say) is a gap in that
    report, not a change of weights.
    """
    (was, was_names), (now, now_names) = _file_keys(old), _file_keys(new)
    moved = {n for n in was.keys() & now.keys() if was[n] != now[n]}
    return sorted(str(n) for n in (was_names ^ now_names) | moved)


def _sha_moved(old, new, key):
    """Both sides hashed the `key` file whole, and the hashes differ."""
    was = (old.get(key) or {}).get("sha256")
    now = (new.get(key) or {}).get("sha256")
    return bool(was and now and was != now)


def _model_files_of(runtime):
    return (runtime or {}).get("model_files") or {}


def model_files_notes(old_rt, new_rt):
    """Other weights or another bundle config behind the same model id.

    For compare(): the runtime key names the server build, not the files. A
    re-pulled GGUF or an edited genie_config.json moves results while the build
    and the model id stay the same, and so does an edited HTP extensions file,
    which sets the perf profile (this host's cache keeps an .orig-backup of
    both beside one bundle, and that genie_config.json was rewritten 36 min
    after its backup: they are edited in place). Silent unless both sides
    recorded files for the same id.
    """
    old, new = _model_files_of(old_rt), _model_files_of(new_rt)
    if not old.get("files") or old.get("model") != new.get("model"):
        return []
    notes = []
    changed = _changed_files(old, new)
    if new.get("files") and changed:
        notes.append(
            f"MODEL FILES CHANGED behind {old['model']}: {', '.join(changed)} — "
            f"a re-pull or another file under the same model id"
        )
    if _sha_moved(old, new, "genie_config"):
        notes.append(
            f"the QAIRT bundle's genie_config.json changed (sampler "
            f"{old['genie_config'].get('sampler')} → "
            f"{new['genie_config'].get('sampler')})"
        )
    if _sha_moved(old, new, "backend_extensions"):
        notes.append(
            "the QAIRT bundle's HTP extensions file changed — it sets the "
            "NPU's perf profile, which moves speed under the same weights"
        )
    return notes


def main(argv=None):
    """Write a lane-runtime file: `python -m orchestrant.benchmark.provenance`."""
    import argparse

    from orchestrant.benchmark.lanes import resolve_lane

    ap = argparse.ArgumentParser(
        description=write_lane_runtimes.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "lanes",
        nargs="+",
        type=resolve_lane,
        metavar="BACKEND|name=URL,model=MODEL",
        help="a backends.json name (geniex-npu) or a full name=URL,model=MODEL spec",
    )
    ap.add_argument("--output", required=True, help="the JSON file to write")
    args = ap.parse_args(argv)
    doc = write_lane_runtimes(args.output, {n: (u, m) for n, u, m in args.lanes})
    for e in doc["lanes"]:
        rt = e["runtime"] or {}
        print(f"  {e['lane']:12s} {runtime_label(rt)}  verified={rt.get('verified')}")
    print(f"  Written to {args.output}; in WSL2 export {LANE_RUNTIMES_ENV}=<its path>")
    return 0


def _lane_process_runtime(proc, exe, model):
    """A GenieX lane process seen on this host: the exact binary and its flags."""
    return {
        "server": "geniex",
        **(_geniex_version(exe) or {}),
        "serve_args": (proc.get("cmdline") or [])[1:],
        # When this process started: two reports with different values
        # were served by different launches, even with identical flags.
        "started": proc.get("started"),
        "verified": True,
        "source": f"lane process pid {proc['pid']}: {exe} --version",
        # A process on this OS reads this OS's model cache.
        **_geniex_serving(model, _geniex_cache_roots(local_only=True)),
    }


def runtime_info(base_url, model=None):
    """WHICH server build produced a measurement, and launched with WHICH flags.

    GenieX v0.5 -> v0.6 changed four behaviours the tooling had encoded (the
    output cap, max_tokens, tool-call parsing, the prefix cache), and every
    published table has carried its version in prose since, because no report
    recorded it. The serve command line matters as much: --nctx, --keepalive
    and --power-mode all move a number, and the CLI's defaults for the first
    two were wrong for benchmarking.

    Best evidence first. The process listening on the lane's port gives the
    exact binary and its flags (`verified: True`). From WSL2 that process is
    invisible: a lane-runtime file written on the host is the next best
    (load_lane_runtime()); without one, a loopback lane that is not Ollama is
    attributed to the INSTALLED GenieX, marked `verified: False` — an upgrade
    mid-session would make the two differ, and the report must not claim more
    than it saw. A GenieX runtime also carries `model_files` for `model`, the
    id the report served (geniex_model_files()), and `drivers`.
    """
    if not base_url:
        return None
    from orchestrant.benchmark.hostload import LaneProcess, _port

    lane = LaneProcess(base_url)
    proc = lane.info() if lane.available else None
    exe = (proc or {}).get("exe") or ""
    # Either separator: a Windows lane's path read on Linux (a report, a test)
    # does not split at backslashes under os.path.
    name = os.path.basename(exe.replace("\\", "/")).lower()
    if proc is not None and name.startswith("geniex"):
        return _lane_process_runtime(proc, exe, model)
    snapshot = load_lane_runtime(base_url, model=model)
    if snapshot:
        return snapshot
    version = _ollama_version(base_url)
    if version:
        return {
            "server": "ollama",
            "version": version,
            "verified": True,
            "source": "/api/version",
        }
    # Only a lane that ANSWERS may be attributed to the installed binary: an
    # unreachable loopback URL is not evidence that GenieX serves it.
    installed = None
    if (
        _port(base_url)
        and _server_models(base_url, timeout=2) is not None
        and _serves_geniex_root(base_url)
    ):
        installed = _installed_geniex()
    if installed:
        return {
            "server": "geniex",
            **(_geniex_version(installed) or {}),
            "serve_args": None,
            "verified": False,
            "source": f"installed binary {installed}; {lane.reason}",
            **_geniex_serving(model),
        }
    return None


def runtime_label(runtime):
    """One line naming a runtime, for notes and tables."""
    if not runtime:
        return "unknown"
    if runtime.get("server") == "geniex":
        return (
            f"geniex {runtime.get('cli', '?')} (QAIRT {runtime.get('qairt', '?')}, "
            f"llama.cpp {runtime.get('llama_cpp', '?')})"
        )
    return f"{runtime.get('server', '?')} {runtime.get('version', '?')}"


def _runtime_key(runtime):
    keys = ("server", "cli", "qairt", "llama_cpp", "version")
    return tuple((runtime or {}).get(k) for k in keys)


def _serve_flags(runtime):
    """The serve arguments minus --host: a port move is not a config change."""
    args = list((runtime or {}).get("serve_args") or [])
    if "--host" in args:
        i = args.index("--host")
        del args[i : i + 2]
    return args


def tool_fingerprint(*paths):
    """Hash of the benchmark's own source.

    A ranking can shift because the GRADER changed, not because a model did.
    Without this, that is indistinguishable from a real regression.

    Line endings are normalised first: with core.autocrlf the same commit is
    CRLF in a Windows checkout and LF in WSL, CI or a fresh clone, and the
    hash of identical source must not depend on which one ran it.
    """
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(paths):
        try:
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read().replace(b"\r\n", b"\n"))
        except OSError:
            return None
    return h.hexdigest()[:16]


def busy_lanes(registry_path=None):
    """Which other endpoints were serving while this ran.

    Results shift with what else is running: a CPU lane measured 23.7 tok/s
    alone and 18.6 next to a busy NPU lane. Two runs taken under different load
    are not comparable, and without recording it nobody can tell which was
    which.
    """
    try:
        from orchestrant.benchmark.openai_api import load_backends

        backends, _ = load_backends(registry_path)
    except Exception:
        return None
    # probe:false is the registry's own rule for paid hosts, and this used to
    # ask them anyway on every report. In parallel: on Windows each refused
    # connect waits out its full timeout, and that was ~40 s per report.
    urls = {
        name: entry["base_url"]
        for name, entry in sorted(backends.items())
        if entry.get("base_url") and entry.get("probe", True)
    }

    def answers(url):
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=2):
                return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        up = dict(zip(urls, pool.map(answers, urls.values()), strict=True))
    return [name for name in urls if up[name]]


def energy_proxy():
    """CPU-seconds consumed, as the closest available stand-in for energy.

    Energy per token is the interesting axis on a battery device and the NPU's
    strongest argument over the CPU lane — 165 % of 800 % CPU against 752 % for
    the same work. But this host exposes no power rail: there is no RAPL on
    aarch64 here, no battery discharge counter reachable from WSL2, and the
    Snapdragon's own sensors are not surfaced. Reporting joules would be
    inventing them.

    So: total CPU time, which is proportional to energy for CPU-bound work and
    silent about the NPU's own draw. Recorded as a PROXY under that name, never
    as a measurement, so nobody later mistakes it for one.
    """
    try:
        import resource

        me = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {
            "cpu_seconds_self": round(me.ru_utime + me.ru_stime, 3),
            "cpu_seconds_children": round(kids.ru_utime + kids.ru_stime, 3),
            "note": (
                "CPU time, not joules. This host exposes no power rail "
                "(no RAPL on aarch64, no battery counter through WSL2), and "
                "this number says nothing about NPU or GPU draw."
            ),
        }
    except Exception:
        return None


PROBE_PROMPT = "Write one sentence about the sea."


SPACER_PROMPT = "Reply with the single word: ok"


def determinism_probe(base_url, model, post, prompt=PROBE_PROMPT, max_tokens=48):
    """Send the same request twice, with another between; did the outputs match?

    `post(url, payload) -> dict` is injected so this can run without a server
    (tests) and so callers pick the transport. "Deterministic" here means two
    draws at temperature 0 agreed byte-for-byte — evidence, not proof, and it is
    recorded as such so a --repeats 1 flip can be read for what it is.

    The prompt must leave the model real choices. The first version asked for
    "the single word: ready" in 8 tokens — an answer with almost no entropy,
    which a SAMPLING lane repeats verbatim. On GenieX v0.6.1 it recorded the
    QAIRT lane as deterministic while two open-ended requests at temperature 0
    came back different, and bench_compare then read that lane's single-draw
    flips as real regressions.

    The two draws are NOT sent back to back. On GenieX (v0.6.1 and v0.7.0,
    measured 2026-09-24) an identical request sent twice in a row takes a cache
    path that changes the reply on both lanes — llama.cpp prefills 0 tokens and
    samples the first token from the previous reply's logits; QAIRT reuses
    part of the dialog — while after any other request both answer as if cold.
    A spacer request between the draws measures the sampler, not that bug.
    """
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    spacer = {
        **payload,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": SPACER_PROMPT}],
    }
    outputs = []
    try:
        for i in range(2):
            if i:
                post(f"{base_url}/v1/chat/completions", spacer)
            reply = post(f"{base_url}/v1/chat/completions", payload)
            content = (
                (reply.get("choices") or [{}])[0].get("message", {}).get("content")
            )
            if content is None:
                raise ValueError("reply carried no message content")
            outputs.append(content)
    except Exception as e:
        return {
            "deterministic": None,
            "requests": len(outputs),
            "prompt": prompt,
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    return {
        "deterministic": outputs[0] == outputs[1],
        "requests": 3,
        "spacer": True,
        "prompt": prompt,
        "output_sha256": [hashlib.sha256(o.encode()).hexdigest()[:16] for o in outputs],
        "error": None,
    }


# Windows 11's power-mode slider (the "overlay" on the active plan). It moves
# CPU clocks, so a CPU-lane number without it is missing a condition.
_POWER_OVERLAYS = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "best power efficiency",
    "00000000-0000-0000-0000-000000000000": "balanced",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "best performance",
}


def host_power():
    """AC or battery, and the Windows power mode; None off Windows."""
    if sys.platform != "win32":
        return None
    out = {}
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = _Status()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            out["on_ac"] = {0: False, 1: True}.get(status.ACLineStatus)
            out["battery"] = status.BatteryFlag != -128  # 128: no system battery
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:120]
    try:
        import winreg

        key = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            for name in ("ActiveOverlayAcPowerScheme", "ActiveOverlayDcPowerScheme"):
                try:
                    guid = str(winreg.QueryValueEx(k, name)[0]).lower()
                except OSError:
                    continue
                out[name] = _POWER_OVERLAYS.get(guid, guid)
    except Exception as e:
        out.setdefault("error", f"{type(e).__name__}: {e}"[:120])
    return out


def collect(
    base_url=None,
    tool_files=(),
    extra=None,
    temperature=None,
    seed=None,
    determinism=None,
    tool_sha256_at_start=None,
):
    """Return a provenance block for a report.

    `temperature`, `seed` and `determinism` (a determinism_probe() result) are
    recorded as explicit nulls when the caller does not supply them.
    `tool_sha256_at_start` is the fingerprint taken when the run began: the
    block is written at the END, and a source edited mid-run would otherwise
    stamp the report with code that did not produce its first rows.
    """
    prov = {
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "host": platform.node() or None,
        "os": f"{platform.system()} {platform.release()}"
        if platform.system()
        else None,
        "architecture": platform.machine() or None,
        "python": platform.python_version(),
        # The interpreter's OWN platform. On Windows on ARM an x64 Python runs
        # under emulation and platform.machine() still says ARM64, so without
        # this an emulated grader and a native one look identical.
        "interpreter": sysconfig.get_platform(),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # A dirty tree means the recorded SHA does not describe what actually ran.
        "git_dirty": bool(_git("status", "--porcelain")),
        "base_url": base_url,
        "server_models": _server_models(base_url) if base_url else None,
        # Which server build, and the lane's own serve flags when visible.
        "runtime": runtime_info(base_url) if base_url else None,
        "tool_sha256": tool_fingerprint(*tool_files) if tool_files else None,
        # Everything else that was answering when this started. A lane that was
        # busy slows the one being measured; recording it is the difference
        # between a comparable number and an unexplained one.
        "live_lanes": busy_lanes(),
        # Not energy. See energy_proxy() for why this host cannot measure that.
        "energy_proxy": energy_proxy(),
        "host_power": host_power(),
        "temperature": temperature,
        "seed": seed,
        "determinism_probe": determinism,
    }
    if tool_sha256_at_start and tool_sha256_at_start != prov["tool_sha256"]:
        prov["tool_sha256_at_start"] = tool_sha256_at_start
        prov["source_changed_during_run"] = True
    if extra:
        prov.update(extra)

    required = ("timestamp_utc", "host", "architecture", "git_sha")
    prov["incomplete"] = [k for k in required if not prov.get(k)]
    return prov


def collect_or_error(base_url, tool_files, tool_sha256_at_start=None):
    """Return collect(), or the error that stopped it, never raising.

    For a report that must be written regardless: one without provenance
    beats none, and says why it has none.
    """
    try:
        return collect(base_url, tool_files, tool_sha256_at_start=tool_sha256_at_start)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def _runtime_notes(old_rt, new_rt):
    """A different server build, or a lane launched with different flags."""
    if bool(old_rt) != bool(new_rt):
        return [
            "runtime recorded on one side only — a runtime change cannot be ruled out"
        ]
    if not old_rt:
        return []
    notes = []
    if _runtime_key(old_rt) != _runtime_key(new_rt):
        notes.append(
            f"SERVING RUNTIME CHANGED — {runtime_label(old_rt)} → "
            f"{runtime_label(new_rt)}: a score difference may be the runtime, "
            f"not the model"
        )
    both_seen = (
        old_rt.get("serve_args") is not None and new_rt.get("serve_args") is not None
    )
    if both_seen and _serve_flags(old_rt) != _serve_flags(new_rt):
        notes.append(
            f"lane launched with different serve flags: {_serve_flags(old_rt)} vs "
            f"{_serve_flags(new_rt)}"
        )
    return notes


def _condition_notes(old, new):
    """A report whose own source moved mid-run, and a changed power condition."""
    notes: list[str] = [
        f"{label} run's benchmark source changed WHILE it ran — its first rows "
        f"came from other code than its tool_sha256 names"
        for label, prov in (("the old", old), ("the new", new))
        if prov.get("source_changed_during_run")
    ]
    before, after = old.get("host_power"), new.get("host_power")
    if before and after and before != after:
        notes.append(
            f"host power differs: {before} vs {after} — CPU clocks, and so a "
            f"CPU lane's numbers, move with the Windows power mode"
        )
    return notes


def compare(old, new):
    """Differences between two provenance blocks, worst first.

    Used when diffing two runs: a result that moved while the runtime, the
    grader or the served models also moved is not evidence about the model.
    """
    notes = []
    if old.get("tool_sha256") != new.get("tool_sha256"):
        notes.append(
            "BENCHMARK SOURCE CHANGED — a score difference may be the "
            "grader, not the model"
        )
    notes += _condition_notes(old, new)
    notes += _runtime_notes(old.get("runtime"), new.get("runtime"))
    if old.get("server_models") != new.get("server_models"):
        notes.append(
            "served models differ between the runs (on GenieX /v1/models is the "
            "whole local cache, so a pull alone changes it)"
        )
    if old.get("architecture") != new.get("architecture") or old.get("host") != new.get(
        "host"
    ):
        notes.append("different host or architecture")
    if (
        old.get("live_lanes") is not None
        and new.get("live_lanes") is not None
        and old["live_lanes"] != new["live_lanes"]
    ):
        notes.append(
            f"different lanes were live: {old['live_lanes']} vs "
            f"{new['live_lanes']} — a busy lane slows the one measured"
        )
    if new.get("git_dirty") or old.get("git_dirty"):
        notes.append("at least one run came from a dirty working tree")
    if old.get("git_sha") != new.get("git_sha"):
        notes.append(
            f"repository moved {str(old.get('git_sha'))[:8]} → "
            f"{str(new.get('git_sha'))[:8]}"
        )
    for key in ("temperature", "seed"):
        if old.get(key) != new.get(key):
            notes.append(
                f"{key} differs: {old.get(key)!r} vs {new.get(key)!r} — "
                f"a flip may be sampling, not the model"
            )
    return notes


def known_deterministic(prov):
    """True only when a probe in this provenance block saw two draws agree."""
    probe = (prov or {}).get("determinism_probe") or {}
    return probe.get("deterministic") is True


if __name__ == "__main__":
    raise SystemExit(main())
