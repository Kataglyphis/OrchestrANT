"""What served a measurement: the model files a GenieX id resolved to, and the host's drivers."""

import glob
import hashlib
import json
import os
import pathlib
import sys
from datetime import UTC, datetime


# WSL2 cannot see the lane process; this names a lane-runtime file written on Windows.
LANE_RUNTIMES_ENV = "LLM_LANE_RUNTIMES"

# <cache>/<org>/<repo>/geniex.json maps each variant of a model id to its file.
_GENIEX_CACHE = (".cache", "geniex", "models")

# Multi-GB GGUFs share their first MiB across quants: hash the head plus sampled windows.
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
    """Name, size, mtime and a sha256 -- whole, or of the first MiB and a sample."""
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
    """The model caches to look in: this OS's, and from WSL2 the Windows host's first."""
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
    """A QAIRT bundle's genie_config.json (hashed whole: edited in place), ctx-bins and more."""
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
    """WHICH files a GenieX model id resolves to in the lane's cache; never raises."""
    if not model:
        return None
    try:
        return _model_files(model, roots or _geniex_cache_roots())
    except Exception as e:  # a manifest in a shape this does not know
        return {"model": model, "error": f"{type(e).__name__}: {e}"[:200]}


# Setup classes whose Control\Class keys give driver versions without admin or a subprocess.
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
    """The NPU and GPU driver versions under a Windows lane, or None with the reason."""
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
