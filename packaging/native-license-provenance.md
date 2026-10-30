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
