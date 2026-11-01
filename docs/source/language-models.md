# Language models

`orchestrant/genai/` runs a GenAI model directory on the chain's ONNX Runtime GenAI wheel and follows
the family's text contract,
[ANTfrastructure's `docs/language-ai.md` § The text contract](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/language-ai.md#the-text-contract):
messages in, decoded pieces out as they are generated, a cancel from any thread, and the speed figures.
Two commands sit on it, `orchestrant-chat` and `orchestrant-serve`; `orchestrant-bench` measures the
second like any other endpoint.

## The model

Models come from ModelANT's releases, never from this repository. `resources/models/llm.lock` pins
the archive by SHA-256, and the hub's fetcher unpacks it:

```bash
python third_party/ANTfrastructure/linux/scripts/lib/fetch-data-lock.py \
  --lock resources/models/llm.lock --dest models --extract
# the GenAI model directory is models/smollm2_135m_instruct-cpu/
```

## The engine

`GenaiEngine(model_dir, provider)` loads the directory on `cpu`, `cuda` or `dml`.
`generate(messages, options, on_text)` applies the model's own chat template, then decodes at most
`options.max_new_tokens` tokens and hands each piece to `on_text`. Temperature 0, the default,
decodes greedily. The result holds the text, the token ids, the prompt and completion token counts,
the time to the first token, prefill included, and the decode rate after it.

`GenerationOptions` also carries `seed` (GenAI's `random_seed`; the same seed repeats a sampled
answer), `json_schema` (the schema's JSON text, handed to llguidance through `set_guidance`) and
`stop` (strings that end the answer before them; text that could still become one is held back,
so no streamed piece carries part of a stop string).

`finish_reason` is `stop` at an end token from `genai_config.json` or a stop string, `length` when
the budget ran out, and `cancelled` after `cancel()` or the caller's own `cancel` event. A cancel
ends the run after its current token; `generate(..., cancel=event)` ends only that run, so one
caller's cancel never reaches the next one. One generation runs at a time and concurrent callers
wait. Each failure raises `GenaiError` with its `kind`: `invalid_options`, `context_length_exceeded`
(prompt plus budget over `context_length`), `json_schema_unsupported` (a GenAI without llguidance,
as on Windows), `generation_failed` (a GenAI error mid-answer), `model_load_failed`,
`provider_unavailable`, `chat_template_failed` or `library_unavailable`.

Not yet: embeddings.

## The commands

```bash
orchestrant-chat --model-dir models/smollm2_135m_instruct-cpu --prompt "What is the capital of France?"
orchestrant-serve --model-dir models/smollm2_135m_instruct-cpu        # http://127.0.0.1:18200/v1
orchestrant-bench speed --backend chain-genai --stream --prompts 10
```

`orchestrant-chat` streams the answer to stdout and the figures to stderr. Without `--prompt` each
stdin line is one turn of a single conversation. `--seed N`, `--json-schema FILE` and `--stop TEXT`
(repeatable) set the options above.

`orchestrant-serve` answers `GET /v1/models` and `POST /v1/chat/completions`, whole or as a
server-sent stream with `stream_options.include_usage`. Each answer carries a `timings` object with
the time to the first token and the decode rate, which OpenAI's format has no field for. An invalid
request gets 400 and names its problem; a provider the library lacks gets 503 and other failures
500. `/v1/embeddings` answers 501. The server listens on 127.0.0.1 unless `--host` says otherwise.

It forwards OpenAI's `seed`, `stop` (a string or a list) and `response_format` (`text`,
`json_object`, or `json_schema` with a `schema` object). The answers it names:

| Request | Answer |
|---|---|
| `model` other than the one it holds | 404 `model_not_found` |
| `max_tokens` Infinity, a malformed seed, stop or response_format | 400 `invalid_options` |
| prompt plus `max_tokens` over the model's context | 400 `context_length_exceeded` |
| a schema on a GenAI without llguidance | 400 `json_schema_unsupported` |
| a GenAI `RuntimeError` | 500 `generation_failed` |
| a failure inside a stream | an `error` event, then `[DONE]` |
| `OPTIONS` | 204; with `--cors-origin ORIGIN`, the CORS headers for that one origin |
| a missing or wrong Bearer token under `--api-key-env NAME` | 401 `unauthorized` |

A client that goes away cancels only its own generation. At most 8 requests are handled at once,
the rest wait for a slot, and a client that stops sending is dropped after 30 s.
`--api-key-env` naming an unset variable refuses to start rather than serve without the key.

`chain-genai` in the hub's
[`backends.json`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/linux/llm-stack/README.md#named-backends)
names that server, so the bench and every other endpoint client reach it by name, next to `ollama`
and the `geniex-*` lanes. On 2026-10-30, SmolLM2-135M on the CPU of the amd64 build host
in the Linux image answered three short prompts at 152 to 184 tokens/s, measured with
`orchestrant-bench speed --prompts 3`.

## Tests

`tests/unit/genai/` runs the server and the CLI against a fake engine on every leg, and the engine
on [the hub's tiny GenAI model](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/language-ai.md#the-test-model),
whose `fixture.json` holds every answer. Without the GenAI wheel those cases skip; the Linux test
legs set `ORCHESTRANT_REQUIRE_GENAI=1` in `scripts/linux/ci_tests.sh`, which turns that into a
failure on GIL legs off riscv64, the ones whose lock installs the wheel.

`tests/unit/genai/test_engine_fake_genai.py` runs `GenaiEngine` itself over a fake
`onnxruntime_genai` module, so its decode loop, provider choice, cancel and error kinds are
checked on every leg. The `genai.*` and `yolo.*` recipes in
`scripts/linux/tests/package-license-mutations.json` mutate the engine, the server and the YOLO
pre- and postprocessing, and `package-license-mutations.yml` requires that each mutant fails these
fake-engine tests; they run under `uv run --frozen --python 3.13 --extra test`, as numpy has no
cp315 wheel for the runner's pinned 3.15. They do not cover the real GenAI wheel or an ONNX session.

- **Options on a fake module, every leg:** `test_options.py` runs `GenaiEngine` on a stand-in for
  `onnxruntime_genai` and checks that seed, schema and greedy decoding reach GenAI. The mutation lane
  runs it for the `genai.*` recipes in `scripts/linux/tests/package-license-mutations.json`.
- **GenAI required:** a repeated seed repeats the ids; seeds 1 to 4 at temperature 1.0 give at least
  two distinct 24-token sequences; a stop string ends with `stop`; the schema answer parses and
  validates. The schema tests use GenAI with llguidance where GenAI is required and a fake module
  without it elsewhere, so on Windows they assert `json_schema_unsupported` instead of skipping.
- **The real model:** `ci_tests.sh` fetches SmolLM2-135M with the hub's
  `fetch-data-lock.py --lock resources/models/llm.lock --extract` and ModelANT's
  `text-generation-v1.json` from `ai-regression.lock`, and sets `ORCHESTRANT_REQUIRE_MODEL=1`.
  `test_contract.py` then asserts the case `capital-of-france` decodes exactly
  `[504, 3575, 282, 4649, 314, 7042, 30, 2]` and contains `Paris`.
- **The packaged smoke check** `orchestrant-smoke` greedy-decodes the model directory that
  `ORCHESTRANT_SMOKE_GENAI_MODEL` names and compares the ids with its `fixture.json`; without one it
  warns that `generate()` is unproven.

## The weekly AI regression run

`.github/workflows/ai-regression.yml` runs `scripts/linux/ai-regression.sh` in `:latest` every
Monday and on dispatch. It fetches SmolLM2-135M from `llm.lock`, the detector from `models.lock` and
ModelANT's `text-generation-v1.json` and `two_cats.jpg` at one commit from
`scripts/linux/ai-regression.lock`, and writes the hub's tiny GenAI model. Then:

- `orchestrant-bench contract` probes `orchestrant-serve` on each model. The tiny model runs only the
  checks whose requests fit its 512-token context; SmolLM2 runs every check but
  `completions_stop_honoured`, since the server has no `/v1/completions` route.
  `scripts/linux/ai_regression_verdicts.py` writes every answer to the summary and fails on `error`;
  yes, no, inconclusive and skipped are recorded, not graded.
- `ai_regression_run.py replay` greedy-decodes each text-generation-v1 case and fails unless the
  token ids equal `expected_ids` and the text contains `must_contain`.
- `ai_regression_run.py detector` runs the packaged diagnostic's production pre- and postprocess
  twice on `two_cats.jpg`, and the hub's `compare_detector_parity.py` requires both cats.
- SmolLM2's request and first-token p50/p95 and the detector's go to bench-history as
  `ai-regression-x64`, without a gate. The reports are artifact `ai-regression` (90 days).

It covers the CPU provider only, and a hosted runner's wall time is no verdict. A prompt plus budget
over a model's context is refused with 400 `invalid_options`; the first run found the server dropping
the connection instead.
