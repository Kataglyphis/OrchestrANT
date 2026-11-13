# Standalone Python native license texts

These texts cover the native closure observed on 2026-10-30 with the cached
ROCm image, its Ubuntu PCRE2 10.46-1build1, and `uv`'s CPython 3.14.4
standalone runtime. They are copied into packages by `app.json`; the native
inventory refers to the copies. The version-specific inventory must be checked
again whenever the Python runtime or image changes: Tcl and Tk's library names
do not identify their patch versions, and the PCRE2 SONAME does not identify its
package version.

| Text | Original |
| --- | --- |
| `tcl-9.0.3-LICENSE.txt` | [Tcl 9.0.3 release](https://github.com/tcltk/tcl/blob/core-9-0-3/license.terms) |
| `tk-9.0.3-LICENSE.txt` | [Tk 9.0.3 release](https://github.com/tcltk/tk/blob/core-9-0-3/license.terms) |
| `itcl-4.3.5-LICENSE.txt`, `itcl-4.3.5-DOC-LICENSE.txt` | `license.terms` and `doc/license.terms` in the [Itcl 4.3.5 original source archive](https://archive.ubuntu.com/ubuntu/pool/universe/i/itcl4/itcl4_4.3.5.orig.tar.gz), whose SHA-256 `c7cc5e2397196316b3cf3f25f3f519ecbba4cd04ee2002598d175514c240e731` matches the [Ubuntu source descriptor](https://archive.ubuntu.com/ubuntu/pool/universe/i/itcl4/itcl4_4.3.5-1.dsc) |
| `thread-3.0.4-LICENSE.txt` | [Tcl Thread 3.0.4 release](https://github.com/tcltk/thread/blob/thread-3-0-4/license.terms) |
| `pcre2-10.46-LICENSE.txt` | [PCRE2 10.46 release](https://github.com/PCRE2Project/pcre2/blob/pcre2-10.46/LICENCE.md) |
| `pcre2-10.46-COPYRIGHT.txt` | `/usr/share/doc/libpcre2-8-0/copyright` in the cached ROCm image, package `libpcre2-8-0` version `10.46-1build1` |

The generated `THIRD-PARTY-NOTICES.txt` must still be regenerated from the full
set of actual release packages. Adding these inventory rows does not certify a
new image or establish that its native components are the same versions.

The cached Windows NVIDIA image digest `b472f91ba9553937a1c62f476ef6d01a878db4d44c754091ece08fb21ba11777`
was inspected on 2026-10-30. Its OpenCV closure carries cuBLAS, cuFFT and NPP
from CUDA 13.4 and cuDNN 9.27.0.42; all 13 observed DLLs matched both the image's
runtime copy and installed SDK originals by SHA-256. OpenCV's DNN and CUDA DLLs
directly import these libraries, so removing them from the closure would leave
missing dependencies. The package-time verifier checks each shipped NVIDIA DLL
and license against the image SDK and rejects a version or byte mismatch.

| Text | Image original | SHA-256 |
| --- | --- | --- |
| `nvidia-cuda-13.4-LICENSE.txt` | `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4\LICENSE` | `088381bc2d891e719a2a9398645b00bb45f3b24231473a8283ac7e3e66b8a028` |
| `nvidia-cudnn-9.27.0.42-LICENSE.txt` | `C:\Program Files\NVIDIA\CUDNN\v9.27.0.42\LICENSE` | `295a1b7118d3417f5d6a943e5f87b59e2a0631fb277129c3b15dee6248adc7cf` |

`app.json` ships these two texts in every package, including CPU variants. Their
component inventory entries apply only when the corresponding NVIDIA DLLs are
present. ANTfrastructure backlog CON182 tracks a stable image-side handoff.

## The MIGraphX plugin's texts

`libmigraphx-ep.so`, AMD's plugin execution provider, is built in the hub's ROCm image, and the
hub's bundle builder copies it into an AMD bundle. The image keeps the licence texts of the module
and of every source archive fetched for it under `${ORT_AMDGPU_EP_ROOT}/licenses`, with
`provenance.txt` beside them. No hub helper copies that tree into a Python bundle, and `app.json`
can only name files of this checkout. So the texts are tracked here as `onnxruntime-ep-amdgpu-*`
and shipped in every package, as the NVIDIA texts are. Their inventory entry applies only where
the module is present.

They were taken on 2026-11-13 from the source archives the pinned hub names, each archive checked
against the hub's SHA-256 pin, by the rule of the hub's build (`plugin_ep_install_module` and
`plugin_ep_seed_licenses` in
`linux/scripts/03-media/build/onnxruntime/build/35-build-migraphx-plugin-ep.sh`): `LICENSE` and
`LICENSES/` of the plugin, and every top-level `LICENSE*` file of each archive in the `amdgpu` rows
of `plugin-ep-seeds.txt`. The ROCm image is x64 only and was not on the machine, so these bytes
have not been compared with the image's tree. `provenance.txt` is not shipped: its two facts are
the first two pins below.

`tests/unit/test_third_party_notices.py` fails when a pin below is not the pinned hub's, or a text
is not the recorded bytes. Then take the texts again, from the archives or from the image's tree,
and update the block and the table.

```text
ORT_AMDGPU_EP_COMMIT=58afd43bc4c2ddc7582712e1d75cbfd4c3bb03fc
ORT_AMDGPU_EP_SOURCE_SHA256=b3a2f00cb10c2447d267ff41f9dfdb30736d24122be46f57035cf7478a876233
ORT_AMDGPU_EP_FMT_VERSION=12.1.0
ORT_AMDGPU_EP_FMT_SHA256=695fd197fa5aff8fc67b5f2bbc110490a875cdf7a41686ac8512fb480fa8ada7
ORT_AMDGPU_EP_GSL_VERSION=4.2.1
ORT_AMDGPU_EP_GSL_SHA256=c9291d95f5f6e5c561990d06a589b01d89e553d0f366f0ce723dd1788e7a6076
ORT_AMDGPU_EP_JSON_VERSION=3.12.0
ORT_AMDGPU_EP_JSON_SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
ORT_AMDGPU_EP_ZLIB_VERSION=1.3.1
ORT_AMDGPU_EP_ZLIB_SHA256=38ef96b8dfe510d42707d9c781877914792541133e1870841463bfa73f883e32
ORT_AMDGPU_EP_PROTOBUF_VERSION=34.1
ORT_AMDGPU_EP_PROTOBUF_SHA256=a79296d3ee43cab5cf72fe702ba8822a607720704c439fad6f285a37f51550b4
ORT_AMDGPU_EP_ABSEIL_VERSION=20250512.1
ORT_AMDGPU_EP_ABSEIL_SHA256=9b7a064305e9fd94d124ffa6cc358592eb42b5da588fb4e07d09254aa40086db
ORT_AMDGPU_EP_ONNX_VERSION=1.21.0
ORT_AMDGPU_EP_ONNX_SHA256=b8b56570d94ecce49fee94e6380db456bb42035722b967015c9dd6459359433e
ORT_AMDGPU_EP_FLATBUFFERS_VERSION=25.12.19
ORT_AMDGPU_EP_FLATBUFFERS_SHA256=f5d4636bfc4d30c622c9ad238ce947848c2b90b10aecd387dc62cdee2584359b
ORT_AMDGPU_EP_RANGE_V3_VERSION=0.12.0
ORT_AMDGPU_EP_RANGE_V3_SHA256=cbcb96beda464e71d293c07dec89ef5c0790ca83d37b0e199890893019441044
```

| Text | Original | SHA-256 |
| --- | --- | --- |
| `onnxruntime-ep-amdgpu-LICENSE` | `LICENSE` of the plugin's source archive | `d42ce99fc62b84c66c7cc7a3e41a7254f3bc340a6a275ad8bf5c6b74150f715d` |
| `onnxruntime-ep-amdgpu-LICENSES-Apache-2.0.txt` | `LICENSES/Apache-2.0.txt` of the plugin's source archive | `b31f1f78b8df12b475cbbd5835fb0a558d1ad2dc5cddf417cfbf09fdfc3dcc10` |
| `onnxruntime-ep-amdgpu-LICENSES-BSD-3-Clause.txt` | `LICENSES/BSD-3-Clause.txt` of the plugin's source archive | `5a93d5831e1297ab10fe643e1a631e83be392896da14ee2951285a79012df69d` |
| `onnxruntime-ep-amdgpu-LICENSES-LLVM-exception.txt` | `LICENSES/LLVM-exception.txt` of the plugin's source archive | `e34c58338bd89d43e709e226610d8f32b3e3c47f4ad9a99a8dc1d4ac7842488e` |
| `onnxruntime-ep-amdgpu-LICENSES-MIT.txt` | `LICENSES/MIT.txt` of the plugin's source archive | `508a77d2e7b51d98adeed32648ad124b7b30241a8e70b2e72c99f92d8e5874d1` |
| `onnxruntime-ep-amdgpu-LICENSES-MPL-2.0.txt` | `LICENSES/MPL-2.0.txt` of the plugin's source archive | `66a3107d5ad6a058aab753eaac2047ccb2ed0e39465dd0fe5844da3e300d5172` |
| `onnxruntime-ep-amdgpu-absl-LICENSE` | `LICENSE` of the Abseil archive | `c79a7fea0e3cac04cd43f20e7b648e5a0ff8fa5344e644b0ee09ca1162b62747` |
| `onnxruntime-ep-amdgpu-flatbuffers-LICENSE` | `LICENSE` of the FlatBuffers archive | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `onnxruntime-ep-amdgpu-fmt-LICENSE` | `LICENSE` of the fmt archive | `07580f2a3b35709ce703d523f447b242f6dfec7582a8c0df102c7fa2849375f8` |
| `onnxruntime-ep-amdgpu-gsl-LICENSE` | `LICENSE` of the GSL archive | `4c21dc82a1c7186ea754fd59d504c00947953a3401fe8e24eaa44109ef8e2555` |
| `onnxruntime-ep-amdgpu-json-LICENSE.MIT` | `LICENSE.MIT` of the nlohmann/json archive | `46a65cffd1ea955132d95a8dd921640714a8d6b537d2e4e482d31145ae95b603` |
| `onnxruntime-ep-amdgpu-onnx-LICENSE` | `LICENSE` of the ONNX archive | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `onnxruntime-ep-amdgpu-protobuf-LICENSE` | `LICENSE` of the Protocol Buffers archive | `6e5e117324afd944dcf67f36cf329843bc1a92229a8cd9bb573d7a83130fea7d` |
| `onnxruntime-ep-amdgpu-protobuf-utf8_range-LICENSE` | `third_party/utf8_range/LICENSE` of the Protocol Buffers archive. libprotobuf links utf8_range; the hub's rule copies top-level files only, so the image's tree has no copy | `02de69b64fc36d9e938f418e52723e42f0b2b226d58a9cb3c8dcbdf7059f5074` |
| `onnxruntime-ep-amdgpu-range-v3-LICENSE.txt` | `LICENSE.txt` of the range-v3 archive | `2b74ee37dd1a90b2b385534b7dbc171938409bda468673a123ff25302c9c5739` |
| `onnxruntime-ep-amdgpu-zlib-LICENSE` | `LICENSE` of the zlib archive | `845efc77857d485d91fb3e0b884aaa929368c717ae8186b66fe1ed2495753243` |
