# Packaged detector diagnostic

`detector_parity.py` exercises the installed bundle's actual YOLO preprocessing,
profile-enabled session and postprocessing twice on the same decoded image and same
session. It writes two results in ANTfrastructure's detector parity schema and an ORT
trace in a new output directory. It intentionally sets confidence to 0.25 for the
shared two-cats contract; the monitor's normal command-line default is 0.5.

The fixture is ModelANT `contracts/fixtures/two_cats.jpg`: its NOTICE records OxidANT
commit `e4e6173af17d68c93eae098b89703cfa594493ac`, the MIT license and SHA-256
`6d375ac20fadfd400b3033084f8b0581dcc145f7c14e3b24b97975e379c52999`.
Use the fixture and its license and notice from that contract; this repository does not
copy it. The diagnostic verifies the fixture hash and the supplied model hash before
running. The supplied model hash must come from the trusted `resources/models/models.lock`;
verify the package's model, LICENSE and NOTICE using ANTfrastructure's locked-files
verifier as well. The diagnostic rejects imported Python modules outside the installed
bundle; native runtime and host GPU library provenance need separate verification.
For CI, fetch `contracts/fixtures/{two_cats.jpg,LICENSE,NOTICE}` from immutable ModelANT
commit `7bb8c9f5b4c8a74632c850b0066dc8ec2adf421f` and verify the image hash; no sibling
checkout or ModelANT submodule is required.

Run the script using the installed bundle's `runtime/bin/python3 -I`, with
`ORCHESTRANT_DATA_DIR` set to that bundle's `share/orchestrant` directory. Specify
`--fixture`, the locked `--model-sha256`, `--device cpu`, and a new `--output`
directory; repeat with `--device migraphx` or `--device cuda` and a second new output.
Use a clean environment without library or ORT provider overrides. A failure or an
existing output directory stops the run. Old bundles that lack the profiling-enabled
`orchestrant.yolo.providers.create_session` API are unsupported; do not substitute an
independent session implementation and call it a production-path check.

Successful captures alone do not establish parity or verify a package. Use the trusted,
recorded ANTfrastructure revision to run `shared/python/verify_ort_profile.py` on each
trace with the corresponding `--device` and `--min-runs 2`. Run
`shared/python/compare_detector_parity.py` for CPU repeat 1 against CPU repeat 2, both
GPU repeats against CPU repeat 1, and GPU repeat 1 against GPU repeat 2. Every command
must succeed. A missing verifier, stale pin, failed capture, missing profile or failed
comparison means no verified result. The current OrchestrANT hub pin predates these
shared checks, so an overlay is prospective evidence only until the pin is updated.
