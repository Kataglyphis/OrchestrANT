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

`finish_reason` is `stop` at an end token from `genai_config.json`, `length` when the budget ran
out, and `cancelled` after `cancel()`. A cancel ends the run after its current token; each run
clears it as it starts. One generation runs at a time and concurrent callers wait. Each failure
raises `GenaiError` with its `kind`: `invalid_options`, `model_load_failed`, `provider_unavailable`,
`chat_template_failed` or `library_unavailable`.

Not yet: `seed`, a JSON schema for constrained output, and embeddings.

## The commands

```bash
orchestrant-chat --model-dir models/smollm2_135m_instruct-cpu --prompt "What is the capital of France?"
orchestrant-serve --model-dir models/smollm2_135m_instruct-cpu        # http://127.0.0.1:18200/v1
orchestrant-bench speed --backend chain-genai --stream --prompts 10
```

`orchestrant-chat` streams the answer to stdout and the figures to stderr. Without `--prompt` each
stdin line is one turn of a single conversation.

`orchestrant-serve` answers `GET /v1/models` and `POST /v1/chat/completions`, whole or as a
server-sent stream with `stream_options.include_usage`. Each answer carries a `timings` object with
the time to the first token and the decode rate, which OpenAI's format has no field for. An invalid
request gets 400 and names its problem; a provider the library lacks gets 503 and other failures
500. `/v1/embeddings` answers 501. A failed write to a client that went away cancels the generation. The server
listens on 127.0.0.1 unless `--host` says otherwise.

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
