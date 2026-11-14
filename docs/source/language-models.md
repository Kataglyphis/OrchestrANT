# Language models

`orchestrant/genai/` runs a GenAI model directory on the chain's ONNX Runtime GenAI wheel and follows
the family's text contract,
[ANTfrastructure's `docs/language-ai.md` § The text contract](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/language-ai.md#the-text-contract):
messages in, decoded pieces out as they are generated, a cancel from any thread, and the speed figures.
`orchestrant-chat` and `orchestrant-serve` sit on it, and `orchestrant-bench` measures the second like any
other endpoint. `orchestrant-text`, `orchestrant-docs` and `yolo-monitor --describe` use it for
[fixed tasks](#text-tasks-german-and-english), [a document tree](#the-nas-document-ai) and
[the camera's scene](#describing-the-scene).

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
`provider_unavailable`, `chat_template_failed` or `library_unavailable`. `prompt_tokens(messages)`,
`context_tokens` and `control_tokens` let a caller fit a prompt and fence its data.

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
| a stream that fails before its first token | its own status and JSON error, as without a stream: a stream's 200 and headers go out with the first token |
| a stream that fails after its first token | an `error` event, then `[DONE]` |
| `OPTIONS` | 204; with `--cors-origin ORIGIN`, the CORS headers for that one origin |
| a missing or wrong Bearer token under `--api-key-env NAME` | 401 `unauthorized` |
| no turn at the model within 300 s, while other answers run | 503 `busy` with `Retry-After: 30` |

A client that goes away cancels only its own generation, streamed or whole: a whole answer checks
after each token whether its client is still there. At most 8 requests are handled at once, the
rest wait for a slot; of those, one at a time runs on the model and the others wait for their turn
up to 300 s, then get 503 `busy`. A client that stops sending is dropped after 30 s.
`--api-key-env` naming an unset variable refuses to start rather than serve without the key.

`chain-genai` in the hub's
[`backends.json`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/linux/llm-stack/README.md#named-backends)
names that server, so the bench and every other endpoint client reach it by name, next to `ollama`
and the `geniex-*` lanes. On 2026-10-30, SmolLM2-135M on the CPU of the amd64 build host
in the Linux image answered three short prompts at 152 to 184 tokens/s, measured with
`orchestrant-bench speed --prompts 3`.

## Text tasks, German and English

```bash
orchestrant-text classify --model-dir DIR --input brief.docx --labels rechnung,brief,sonstiges
orchestrant-text entities --model-dir DIR --text "..." --language de
orchestrant-text summarize --model-dir DIR --max-chars 400 < letter.txt
orchestrant-text translate --model-dir DIR --input note.md --to de
```

`orchestrant.genai.text_tasks.TextTasks` does the same from Python. Each prints one JSON line. Every answer
is bound by a closed JSON schema through llguidance and checked again here, since an unguided model could
break it:

| Task | Answer | The check |
|---|---|---|
| `classify` | `label` | one of `--labels`, by the schema's `enum` and by comparison |
| `entities` | `entities`, `rejected_entities` | a name the text does not contain, spaces and case aside, is moved to `rejected_entities` |
| `summarize` | `summary` | a string of at most `--max-chars` |
| `translate` | `translation` | a string of at most `--max-chars`; `--to` is `de` or `en` |

Every report carries `language` and `language_source` (`requested`; `detected` by counting function words;
`fallback`, English, when those decide nothing), `truncated`, `suspect_instructions`, `finish_reason` and
`tokens`. A document over the context is cut from its end to fit beside `--max-new-tokens`, with
`truncated: true`: the summary covers the head only, and no chunking stitches a long document together.
An answer that hits its budget is `answer_truncated`, a malformed one `invalid_answer`, a cancelled one
`cancelled`; none becomes a value. `--input` reads text, Office and PDF files through the
[extractor](#the-nas-document-ai). On Windows GenAI has no guidance (hub LAI-H2), so every task answers
`json_schema_unsupported` there.

Nothing checks that a label fits, a summary is faithful or a translation is right.

## Documents are data

A document, a file name and a detector's class names cannot become instructions by construction, as far as
construction reaches (`orchestrant/genai/untrusted.py`):

- **Fixed rules, separate data.** The system message never holds document text; a test compares it with and
  without an attack, byte for byte.
- **No control tokens.** A tokenizer turns `<|im_end|>` in a prompt into the control id that ends a turn,
  whatever message it came from. The model's own added tokens from `tokenizer.json` and the common shapes
  (`<|...|>`, `<...>`, `[INST]`) are split with a word joiner before tokenizing. The tiny model proves it on the
  real tokenizer: a split token stays one id per byte.
- **A fence it cannot close.** The markers carry the hash of the text inside, so a run repeats and the text
  cannot contain its own closing marker.
- **Nothing to do but answer.** No tools, no network, no file writes follow from an answer. Labels and citations
  are limited to fixed sets. A number the prompt never offered is an error.
- **Orders are noticed.** `reads_like_orders` matches phrases such as "ignore the instructions" or "ignoriere die
  Regeln". `ask` withholds such a source and names it in `withheld`; `--include-suspect` takes it back. The tasks
  set `suspect_instructions`.

**What this does not do: it does not make a model immune.** A paraphrase passes the phrase match, and a model can
still follow text it reads. With a poisoned source let back in through `--include-suspect`, SmolLM2-135M answered
exactly what the source demanded, inside the valid schema. Treat every answer as untrusted output: show it, do not
act on it.

## The NAS document AI

The extraction, full-text and answering half of
[`benchmarks/docs/nas-document-ai.md`](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/docs/nas-document-ai.md).
The path is a parameter and nothing holds a host name: mount the share, then point at the mount.

```bash
orchestrant-docs index --root /path/to/mounted/share --db ~/nas-index.db
orchestrant-docs search "Rechnung 4711" --db ~/nas-index.db
orchestrant-docs ask "Wie lautet die Rechnung 4711?" --db ~/nas-index.db --model-dir DIR
```

**`index`** reads the tree and never writes to it. Keep `--db` on a local disk: it may not live inside the tree,
and SQLite's locks do not hold on CIFS. A second walk reads only what changed by size and modification time. Each
file gets a status, and the report counts them:

| Status | Means |
|---|---|
| `ok` | indexed: txt, md, csv, tsv, log, html, eml, json, xml, docx, xlsx, pptx, and PDF text |
| `needs_ocr` | a PDF with pages whose text layer is missing or broken; its good pages are indexed, the rest are not |
| `unsupported` | no extractor: photos, old `.doc`, and every PDF when PyMuPDF is absent |
| `empty`, `too_large`, `error` | no text; over 256 MiB; a broken, encrypted or badly named file, listed in `file_errors` and retried next walk |

PyMuPDF is AGPL and this package neither depends on nor installs it; install it yourself to read PDFs.

**The index forgets a file only when the walk can be trusted.** An unmounted share looks like an emptied one, so an
empty tree purges nothing without `--allow-empty`; neither does a walk with an unreadable directory or one cut by
`--max-files`. Such a run names the reason in `purge_refused` and exits 1. Other exits: 0, and 2 on an error named
on stderr. Symlinks are not followed, and `@eaDir`, `#recycle`, `.snapshot` and similar trees are skipped
(`--exclude` adds names).

**`search`** is SQLite FTS5 with BM25. No model. Words only reach it quoted, so a question cannot speak FTS5's
syntax. Umlauts fold (`muller` finds `Müller`) and a term of four letters or more matches as a prefix, which reaches
the front of a German compound (`Rechnung`, `Rechnungsnummer`). It is not semantic and does not stem: `Nummer` does
not find `Rechnungsnummer`.

**`ask`** fences the best chunks as data and answers `answer` and `cited`, the sources the model names, beside
`retrieved`, `withheld` and `excerpt_chars`. Excerpts shrink to fit the context, then the weakest source leaves. When
nothing matches, no model is loaded and `reason` is `no_sources`. **A citation proves the number exists, not that the
source supports the answer.** Nothing in these commands opens a connection; a test runs `ask` with `connect` cut.

Left out of this slice: embeddings and semantic search (the embedding model is the one decision that forces a full
re-crawl, and it is the owner's), OCR and the VLM lane for `needs_ocr` pages, images, Docling's layout and tables,
and a run on a real share.

## Describing the scene

```bash
yolo-monitor --describe --describe-model DIR --describe-language de --describe-interval 30 --describe-out scene.jsonl
yolo-monitor --self-test --self-test-image two_cats.jpg --describe --describe-model DIR
```

`--describe` hands each frame's detections to `SceneDescriber`, which counts them by class and asks the model for
one sentence in its own thread, so a frame never waits for it. At most one description per `--describe-interval`,
none for an unchanged scene, and a frame due while one runs is skipped. Each goes to the log, which the Flet viewer
shows, and as a JSON line to `--describe-out`. A model that cannot load stops the start before the camera opens;
one that fails later is logged and counted while the camera keeps running. The model runs on `cpu` unless
`--describe-provider` says otherwise, and without `--describe` no model is loaded.

The model sees counts, nothing else: no image, positions or tracks. The sentence can say no more than that, and
nothing checks that it matches them. The JSON line keeps the counts beside it.

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
fake-engine tests; they run through the hub's `recipe-pytest.sh` inside the family CI image, on
the hub's pinned `PYTHON_VERSION` with its chain-built wheels (owner decision 2026-11-14), not on a
Python typed into the recipe. They do not cover the real GenAI wheel or an ONNX session.

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

- **The tasks, documents and scenes:** `tests/unit/genai/test_text_tasks.py`, `test_untrusted.py`,
  `tests/unit/documents/` and `tests/unit/test_yolo_describe.py` run on a scripted engine, on every leg with no GPU
  and no wheel; their `genai.*`, `documents.*` and `yolo.*` recipes must bite. The `*_tiny.py` suites run them through
  the real wheel and the real tokenizer, asserting `json_schema_unsupported` where guidance is missing.
- **End to end:** `scripts/linux/language_e2e.py` runs every command in the image on the CPU, and the real monitor
  loop with the detector and a stand-in camera. **The tiny model proves the plumbing, not the quality.** It answers
  strings like `#$%&'()*`: the graded facts are shape, repeatability, citations, withheld sources and tokens. No
  quality figure exists for any of these tasks, and SmolLM2's answers in the report are recorded, not graded.

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
- `language_e2e.py` runs the language commands on the tiny model and fails on a problem it names; SmolLM2-135M's
  answers go to `language-e2e.json`, ungraded. PDF text is proved only where PyMuPDF is installed, which the image does
  not do (`pdf_proved` in the report).
- SmolLM2's request and first-token p50/p95 and the detector's go to bench-history as
  `ai-regression-x64`, without a gate. The reports are artifact `ai-regression` (90 days).

It covers the CPU provider only, and a hosted runner's wall time is no verdict. A prompt plus budget
over a model's context is refused with 400 `invalid_options`; the first run found the server dropping
the connection instead.
