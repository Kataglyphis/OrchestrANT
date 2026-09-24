"""Tests for the chat-quality instrument.

A grader that passes a wrong reply, or fails a right one, makes every score
it produces worthless -- so every case carries a known-good and a known-bad
reply here, and a new case without them fails the coverage test. Nothing
opens a socket: client.post_json is replaced by a canned transport.
"""

import contextlib
import io
import json
import os
import sys
import types
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_chat as bc  # noqa: E402

from orchestrant.benchmark import client as bench_cli  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def body(content, finish="stop", usage=None, reasoning=None):
    """A non-streamed chat response the way an OpenAI-compatible server sends it."""
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    usage = usage or {"prompt_tokens": 20, "completion_tokens": 10}
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage}


def reply(data):
    """What client.post_json returns: a context manager whose json() is `data`."""
    return contextlib.nullcontext(types.SimpleNamespace(json=lambda: data))


def http_error(code, text):
    return urllib.error.HTTPError(
        "http://lane/v1/chat/completions", code, "err", {}, io.BytesIO(text.encode())
    )


@pytest.fixture
def transport(monkeypatch):
    """Replace client.post_json. `answer(body) -> response body` or raises."""

    class Transport:
        sent = []

        @staticmethod
        def answer(_request):
            return body("ok")

    def fake(url, payload, entry=None, stream=False, timeout=300, deadline=None):
        Transport.sent.append(payload)
        return reply(Transport.answer(payload))

    Transport.sent = []
    monkeypatch.setattr(bench_cli, "post_json", fake)
    return Transport


CASE = {case["name"]: case for case in bc.CASES}


# ── every case: one reply that must pass, one that must fail ─────────────────

GOOD_BAD = {
    "words_at_most_20": ("Sunlight scatters off air molecules, and blue light scatters most, so the sky looks blue.", "Sunlight is made of many colours, and when it enters the atmosphere the short blue wavelengths are scattered by gas molecules far more strongly than the red ones are."),
    "words_exactly_5": ("Vast, deep, blue, restless, alive.", "Vast and deep blue water everywhere."),
    "one_sentence": ("A thermometer measures temperature.", "It measures temperature. It often uses alcohol."),
    "three_sentences": ("A bicycle has two wheels. You pedal it to move. It is quiet and cheap.", "A bicycle has two wheels. You pedal it."),
    "bullets_exactly_4": ("- Apple\n- Banana\n- Cherry\n- Mango", "Here are four fruits:\n- Apple\n- Banana\n- Cherry\n- Mango"),
    "bullets_at_most_3": ("- Keep a schedule\n- Avoid screens late\n- Keep the room cool", "- Keep a schedule\n- Avoid screens\n- Keep it cool\n- Skip coffee"),
    "answer_in_german": ("Die Hauptstadt von Frankreich ist Paris. Sie ist für den Eiffelturm bekannt.", "The capital of France is Paris. It is famous for the Eiffel Tower."),  # codespell:ignore
    "translate_to_german": ("Die Katze schläft auf der warmen Fensterbank.", "Die cat schläft auf der warmen Fensterbank."),
    "plain_text_paragraphs": ("A hash table stores values under keys.\n\nIt hashes each key to find a slot quickly.", "## Hash tables\n\nA hash table stores **values** under keys."),
    "plain_text_flag": ("The German flag is black, red and gold.", "- black\n- red\n- gold"),
    "capitals_only": ("JUPITER IS THE LARGEST PLANET.", "Jupiter is the largest planet."),
    "fixed_sign_off": ("Mars. Over and out", "Over and out. Mars is red."),
    "avoid_a_word": ("Our moon circles Earth every month. Its craters catch sunlight at night.", "The moon circles Earth. It shines at night."),
    "json_people": ('[{"name": "Tobias", "age": 27}, {"name": "Mara", "age": 31}]', '[{"name": "Mara", "age": "31"}, {"name": "Tobias", "age": 27}]'),
    "json_config": ('{"host": "localhost", "port": 8080, "debug": false, "workers": 4}', '{"host": "localhost", "port": 8080, "debug": false, "workers": 4, "tls": true}'),
    "json_sentiment": ('{"reviews": [{"id": 1, "sentiment": "positive"}, {"id": 2, "sentiment": "negative"}, {"id": 3, "sentiment": "neutral"}]}', '{"reviews": [{"id": 1, "sentiment": "positive"}, {"id": 2, "sentiment": "negative"}, {"id": 3, "sentiment": "mixed"}]}'),
    "json_order_total": ('{"id": "A-17", "items": [{"sku": "PN-4", "qty": 1}, {"sku": "NB-1", "qty": 3}], "total": 17.50}', '{"id": "A-17", "items": [{"sku": "NB-1", "qty": 3}, {"sku": "PN-4", "qty": 1}], "total": 12.5}'),
    "json_booleans": ('{"q1": true, "q2": false, "q3": true}', '{"q1": 1, "q2": 0, "q3": 1}'),
    "json_capitals": ('["Paris", "Berlin", "Luxembourg City"]', '["Paris", "London", "Rome"]'),
    "json_escaping": (json.dumps({"quote": 'She said "hi" and left a \\ behind.'}), '{"quote": "She said hi and left a behind."}'),
    "json_nullable": ('{"name": "Ana Ruiz", "phone": null, "city": "Porto"}', '{"name": "Ana Ruiz", "phone": "unknown", "city": "Porto"}'),
    "recall_turn_one": ("Your favourite number plus 3 is 50, Liesel.", "It is 50."),
    "rule_from_turn_one": ("Cold.", "The opposite of hot is cold."),
    "correction_in_turn_two": ("16", "14"),
}  # fmt: skip


def _doc_replies(case):
    """The value, and its decoy, as an answer to one document question."""
    (_, (want,)), (_, (decoy,)) = case["checks"]
    return want, decoy


class TestEveryCase:
    def test_every_hand_written_case_has_a_good_and_a_bad_reply(self):
        written = {c["name"] for c in bc.CASES if "doc_tokens" not in c}
        assert written == set(GOOD_BAD)

    @pytest.mark.parametrize("name", sorted(GOOD_BAD))
    def test_the_good_reply_passes_and_the_bad_one_fails(self, name):
        good, bad = GOOD_BAD[name]
        assert bc.grade(good, CASE[name]["checks"]) == (True, "ok")
        ok, detail = bc.grade(bad, CASE[name]["checks"])
        assert not ok, detail

    @pytest.mark.parametrize(
        "name", sorted(c["name"] for c in bc.CASES if "doc_tokens" in c)
    )
    def test_a_document_answer_must_name_the_value_and_not_the_decoy(self, name):
        want, decoy = _doc_replies(CASE[name])
        checks = CASE[name]["checks"]
        assert bc.grade(want, checks)[0]
        assert bc.grade(f"It is {want}.", checks)[0]
        assert not bc.grade(decoy, checks)[0]
        # Hedging between the two is not an answer.
        assert not bc.grade(f"{want}, or possibly {decoy}", checks)[0]

    def test_about_thirty_cases_in_the_four_families(self):
        categories = {c["category"] for c in bc.CASES}
        assert {"instruction", "json", "multiturn"} <= categories
        assert {"doc_1k", "doc_3.5k", "doc_8k"} <= categories
        assert 28 <= len(bc.CASES) <= 36
        assert len({c["name"] for c in bc.CASES}) == len(bc.CASES)

    def test_a_json_case_forbids_a_fence_and_says_so(self):
        good = GOOD_BAD["json_booleans"][0]
        ok, detail = bc.grade(f"```json\n{good}\n```", CASE["json_booleans"]["checks"])
        assert not ok
        assert "code fence" in detail

    def test_multi_turn_cases_end_on_the_user_and_refer_back(self):
        for case in (c for c in bc.CASES if c["category"] == "multiturn"):
            roles = [m["role"] for m in case["messages"]]
            assert roles[0] == roles[-1] == "user"
            assert len(roles) >= 5


# ── text measures ────────────────────────────────────────────────────────────


class TestTextMeasures:
    def test_words_count_hyphenated_and_apostrophised_words_once(self):
        assert bc.words("It's a well-known, blue sky.") == [
            "It's",
            "a",
            "well-known",
            "blue",
            "sky",
        ]

    def test_sentences_do_not_split_on_decimals_but_do_after_quotes(self):
        assert len(bc.sentences("It costs 2.50 euros. That is cheap.")) == 2
        assert len(bc.sentences('He said "stop." Then he left.')) == 2
        assert len(bc.sentences("One sentence without a full stop")) == 1

    def test_an_abbreviation_does_not_end_a_sentence(self):
        one = "A thermometer measures temperature, e.g. of air or water."
        assert bc.sentences(one) == [one]
        assert len(bc.sentences("Ask Dr. Lin. She knows, i.e. she measured it.")) == 2
        assert bc.grade(one, CASE["one_sentence"]["checks"]) == (True, "ok")

    def test_a_sign_off_alone_names_no_planet(self):
        ok, detail = bc.grade("Over and out", CASE["fixed_sign_off"]["checks"])
        assert not ok
        assert detail.startswith("contains")

    def test_paragraphs_fall_back_to_lines_when_no_blank_line_separates_them(self):
        assert len(bc.paragraphs("First.\n\nSecond.")) == 2
        assert len(bc.paragraphs("First.\nSecond.")) == 2
        assert len(bc.paragraphs("Only one.")) == 1

    def test_german_votes(self):
        german = "Die Katze ist nicht auf dem Dach."  # codespell:ignore
        assert bc.german_votes(german) == (5, 0)
        assert bc.german_votes("The cat is on the roof.") == (0, 4)

    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("# Title\ntext", "a heading"),
            ("text\n- item", "a list item"),
            ("text\n2. item", "a list item"),
            ("a **bold** word", "bold"),
            ("an *italic* word", "italics"),
            ("use `dict`", "code"),
            ("> quoted", "a quote block"),
            ("see [docs](http://x)", "a link"),
            ("| a | b |", "a table"),
        ],
    )
    def test_markdown_is_named_by_kind(self, text, kind):
        assert bc.markdown_found(text) == kind

    def test_plain_prose_with_a_hyphen_and_an_asterisk_is_not_markdown(self):
        assert bc.markdown_found("A well-known fact: 2 * 3 = 6, a - b.") is None

    def test_mentions_matches_whole_words_and_whole_numbers(self):
        assert bc.mentions("the 16th of May", "16")
        assert not bc.mentions("150 lanterns", "50")
        assert not bc.mentions("Die Katze", "cat")
        assert not bc.mentions("a category", "cat")
        assert bc.mentions("Cobalt Heron.", "cobalt heron")


# ── the JSON Schema subset ───────────────────────────────────────────────────


def _schemas(value):
    """Every schema dict inside a case's check arguments."""
    if isinstance(value, dict):
        if "type" in value or "properties" in value:
            yield value
        for sub in value.values():
            yield from _schemas(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            yield from _schemas(sub)


class TestValidator:
    def test_integer_is_not_a_bool_and_three_point_zero_is_an_integer(self):
        assert bc.validate(value=True, schema={"type": "integer"})
        assert bc.validate(value=False, schema={"type": "number"})
        assert bc.validate(3.0, {"type": "integer"}) == []
        assert bc.validate(3.5, {"type": "integer"})

    def test_a_type_list_and_null(self):
        schema = {"type": ["string", "null"]}
        assert bc.validate(None, schema) == []
        assert bc.validate("x", schema) == []
        assert bc.validate(1, schema)

    def test_enum_and_const_do_not_confuse_true_with_one(self):
        assert bc.validate(1, {"enum": [True]})
        assert bc.validate(value=True, schema={"const": 1})
        assert bc.validate("red", {"enum": ["red", "blue"]}) == []

    def test_object_errors_name_the_path(self):
        schema = bc._obj(a=bc._INT, b={"type": "array", "items": bc._obj(c=bc._STR)})
        errors = bc.validate({"a": 1, "b": [{"c": 5}], "z": 0}, schema)
        assert any("$.b[0].c" in e for e in errors)
        assert any("unexpected 'z'" in e for e in errors)
        assert any("missing 'a'" in e for e in bc.validate({"b": []}, schema))

    @pytest.mark.parametrize(
        ("value", "schema"),
        [
            ([1], {"minItems": 2}),
            ([1, 2, 3], {"maxItems": 2}),
            ("", {"minLength": 1}),
            ("abc", {"maxLength": 2}),
            (0, {"minimum": 1}),
            (70000, {"maximum": 65535}),
        ],
    )
    def test_limits(self, value, schema):
        assert bc.validate(value, schema)

    def test_an_unimplemented_keyword_raises_rather_than_passing(self):
        with pytest.raises(ValueError, match="pattern"):
            bc.validate("x", {"type": "string", "pattern": "^y$"})
        with pytest.raises(TypeError):
            bc.validate(
                {}, {"type": "object", "additionalProperties": {"type": "string"}}
            )

    def test_every_case_schema_uses_only_implemented_keywords(self):
        found = 0
        for case in bc.CASES:
            for schema in _schemas(case["checks"]):
                found += 1
                assert set(schema) <= bc.SCHEMA_KEYWORDS, case["name"]
        assert found >= 8

    def test_parse_json_reply(self):
        assert bc.parse_json_reply(' {"a": 1} ') == ({"a": 1}, None)
        assert "code fence" in bc.parse_json_reply('```json\n{"a": 1}\n```')[1]
        assert "even inside" in bc.parse_json_reply("```\n{a: 1}\n```")[1]
        assert bc.parse_json_reply('Sure! {"a": 1}')[1].startswith("not JSON")

    def test_capitals_must_be_distinct(self):
        checks = CASE["json_capitals"]["checks"]
        ok, detail = bc.grade('["Paris", "paris", "Rome"]', checks)
        assert not ok
        assert "repeats" in detail


# ── documents ────────────────────────────────────────────────────────────────


class TestDocuments:
    def test_the_same_document_every_run(self):
        assert bc.document(3500) == bc.document(3500)
        assert bc.document(1000) != bc.document(3500)

    @pytest.mark.parametrize("nominal", sorted(bc.DOC_SIZES))
    def test_sized_under_the_nominal_and_near_it(self, nominal):
        size = bc.approx_tokens(bc.document(nominal))
        assert 0.9 * nominal <= size <= nominal

    def test_the_npu_sized_document_leaves_room_in_4096(self):
        prompt = CASE["doc_3.5k_lanterns"]["prompt"]
        assert bc.approx_tokens(prompt) < 3600

    @pytest.mark.parametrize("nominal", sorted(bc.DOC_SIZES))
    def test_each_value_and_decoy_appears_once(self, nominal):
        text = bc.document(nominal)
        for fact, (value, wrong) in zip(bc._FACTS, bc.DOC_FACTS[nominal], strict=True):
            assert text.count(fact[1].format(value)) == 1
            assert text.count(fact[2].format(wrong)) == 1
            assert text.count(str(value)) == 1
        final = text.index("The final stocktake")
        assert text.index("An earlier, incomplete count") < final

    def test_filler_numbers_never_collide_with_an_answer(self):
        text = bc.document(8000)
        for value in ("128", "112", "73", "68", "41", "36"):
            assert text.count(value) <= 1

    def test_approx_tokens_counts_digits_singly(self):
        assert bc.approx_tokens("day 1234") == 5  # 1.3 for the word, 1 per digit


# ── one reply: thinking, cuts, grading ───────────────────────────────────────


class TestGradeReply:
    CASE = CASE["doc_1k_door_code"]

    def test_thinking_is_stripped_before_grading(self):
        row = bc.grade_reply(
            self.CASE, body("<think>maybe amber</think>cobalt heron"), 256
        )
        assert row["passed"]
        assert row["answer_preview"] == "cobalt heron"

    def test_an_answer_only_inside_an_unclosed_think_is_no_answer(self):
        row = bc.grade_reply(self.CASE, body("<think>it is cobalt heron"), 256)
        assert not row["passed"]
        assert row["detail"] == "no answer"
        assert not row.get("truncated")
        assert row["answered"] is False

    def test_a_separate_reasoning_field_is_not_the_answer(self):
        row = bc.grade_reply(self.CASE, body("", reasoning="cobalt heron"), 256)
        assert not row["passed"]

    def test_a_reply_cut_at_the_budget_is_not_graded(self):
        cut = body("<think>let me", "length", {"completion_tokens": 256})
        row = bc.grade_reply(self.CASE, cut, 256)
        assert row["truncated"] is True
        assert "passed" not in row
        # No finish_reason at all: reaching max_tokens is read as a cut.
        row = bc.grade_reply(
            self.CASE, body("cob", None, {"completion_tokens": 256}), 256
        )
        assert row["truncated"] is True

    def test_the_hash_covers_thinking_too(self):
        a = bc.grade_reply(self.CASE, body("<think>a</think>cobalt heron"), 256)
        b = bc.grade_reply(self.CASE, body("<think>b</think>cobalt heron"), 256)
        assert a["reply_sha256"] != b["reply_sha256"]


# ── one attempt through the stubbed transport ────────────────────────────────


class TestRunCase:
    def test_a_pass_records_the_usage_and_the_document_size(self, transport):
        transport.answer = staticmethod(
            lambda request: body(
                "silver otter", usage={"prompt_tokens": 3301, "completion_tokens": 4}
            )
        )
        row = bc.run_case("http://lane", "m", CASE["doc_3.5k_door_code"], 0, 512)
        assert row["passed"]
        assert (row["prompt_tokens"], row["doc_tokens"]) == (3301, 3500)
        sent = transport.sent[-1]
        assert (sent["temperature"], sent["max_tokens"], sent["stream"]) == (
            0,
            512,
            False,
        )
        assert "=== DOCUMENT ===" in sent["messages"][0]["content"]

    def test_a_multi_turn_case_sends_its_whole_history(self, transport):
        bc.run_case("http://lane", "m", CASE["recall_turn_one"], 0, 512)
        assert transport.sent[-1]["messages"] == CASE["recall_turn_one"]["messages"]

    def test_a_context_refusal_is_an_overflow_not_a_failure(self, transport):
        def refuse(request):
            raise http_error(400, '{"error":{"code":"context_length_exceeded"}}')

        transport.answer = staticmethod(refuse)
        row = bc.run_case("http://lane", "m", CASE["doc_8k_lanterns"], 0, 512)
        assert row["overflow"] is True
        assert not bc.measured(row)
        assert bc._verdict(row) == "OVERFLOW"

    @pytest.mark.parametrize(
        "error",
        [
            http_error(429, "context window busy, rate limit exceeded"),
            http_error(400, "quota exceeded"),
            http_error(500, "Context create from binary failed"),
            urllib.error.URLError("refused"),
        ],
    )
    def test_anything_else_is_a_transport_error(self, transport, error):
        def fail(request):
            raise error

        transport.answer = staticmethod(fail)
        row = bc.run_case("http://lane", "m", CASE["doc_8k_lanterns"], 0, 512)
        assert (row["errored"], row["overflow"]) == (True, False)

    def test_a_reply_that_is_not_an_object_is_an_error(self, transport):
        transport.answer = staticmethod(lambda request: ["not", "a", "body"])
        row = bc.run_case("http://lane", "m", CASE["one_sentence"], 0, 512)
        assert row["errored"] is True


# ── a whole run ──────────────────────────────────────────────────────────────


def _cases(*names):
    return [CASE[n] for n in names]


class TestEvaluate:
    def test_repeats_are_spaced_and_identical_replies_are_one_observation(
        self, transport, spacers
    ):
        transport.answer = staticmethod(
            lambda request: body("A thermometer measures temperature.")
        )
        cases = _cases("one_sentence", "three_sentences")
        report = bc.evaluate(
            "http://lane", "m", "lane", repeats=3, warmup=False, cases=cases
        )
        assert (
            len(spacers) == 2 * 2
        )  # between repeats of each case, never before the first
        assert report["deterministic"] is True
        assert (report["passed"], report["total"]) == (3, 6)
        assert (report["effective_k"], report["effective_n"]) == (1, 2)
        assert report["categories"]["instruction"]["cases"] == 2

    def test_differing_replies_with_the_same_verdicts_still_count_per_case(
        self, transport
    ):
        replies = iter(
            ["It measures temperature.", "A thermometer measures temperature."]
        )
        transport.answer = staticmethod(lambda request: body(next(replies)))
        report = bc.evaluate(
            "http://lane",
            "m",
            "lane",
            repeats=2,
            warmup=False,
            cases=_cases("one_sentence"),
        )
        assert (report["deterministic"], report["repeats_agreed"]) == (False, True)
        assert report["effective_n"] == 1

    def test_mixed_verdicts_count_every_attempt(self, transport):
        replies = iter(["It measures temperature.", "Heat. Cold."])
        transport.answer = staticmethod(lambda request: body(next(replies)))
        report = bc.evaluate(
            "http://lane",
            "m",
            "lane",
            repeats=2,
            warmup=False,
            cases=_cases("one_sentence"),
        )
        assert (report["effective_k"], report["effective_n"]) == (1, 2)

    def test_rows_nobody_graded_leave_the_denominator_and_are_counted(
        self, transport, capsys
    ):
        def answer(request):
            text = request["messages"][-1]["content"]
            if "=== DOCUMENT ===" in text:
                raise http_error(400, "prompt is too long for the context window")
            if "thermometer" in text:
                return body("<think>hmm", "length", {"completion_tokens": 64})
            return body("A bicycle has two wheels. You pedal it. It is quiet.")

        transport.answer = staticmethod(answer)
        cases = _cases("one_sentence", "three_sentences", "doc_8k_door_code")
        report = bc.evaluate(
            "http://lane", "m", "lane", max_tokens=64, warmup=False, cases=cases
        )
        assert (report["passed"], report["total"]) == (1, 1)
        assert (report["overflow"], report["truncated"], report["errored"]) == (1, 1, 0)
        assert report["categories"]["doc_8k"] == {
            "passed": 0, "total": 0, "excluded": 1, "cases_passed": 0, "cases": 0,
        }  # fmt: skip
        out = capsys.readouterr().out
        assert "1/1 = 100% [" in out
        assert "OVERFLOW" in out
        assert "raise --max-tokens" in out

    def test_a_failed_warmup_does_not_stop_the_run(self, transport):
        calls = []

        def answer(request):
            calls.append(request["max_tokens"])
            if len(calls) == 1:
                raise urllib.error.URLError("loading")
            return body("A thermometer measures temperature.")

        transport.answer = staticmethod(answer)
        report = bc.evaluate("http://lane", "m", "lane", cases=_cases("one_sentence"))
        assert calls[0] == 16
        assert report["passed"] == 1

    def test_the_report_reads_back_through_bench_compare(self, transport):
        from bench_compare import normalise

        transport.answer = staticmethod(
            lambda request: body("A thermometer measures temperature.")
        )
        row = bc.evaluate(
            "http://lane", "m", "lane", warmup=False, cases=_cases("one_sentence")
        )
        entry = normalise({"benchmark": "bench_chat", "reports": [row]})["entries"][0]
        assert entry["cases"] == {"one_sentence": (1, 1)}


# ── main ─────────────────────────────────────────────────────────────────────

CANDIDATES = [
    {"label": "npu", "backend": "geniex-npu", "base_url": "http://npu", "model": "q", "entry": {}},
    {"label": "cpu", "backend": "geniex-cpu", "base_url": "http://cpu", "model": "g", "entry": {}},
]  # fmt: skip


@pytest.fixture
def wired(monkeypatch, transport):
    """main() with the candidates, the probe and the report write stubbed."""
    written = {}
    monkeypatch.setattr(
        bench_cli, "candidate_rows", lambda *a, **k: [dict(c) for c in CANDIDATES]
    )
    monkeypatch.setattr(
        "orchestrant.benchmark.provenance.determinism_probe",
        lambda *a, **k: {"deterministic": False, "requests": 3, "error": None},
    )

    def write_report(
        path, benchmark, config, reports, base_url, tool_files, extra=None, *, run_start
    ):
        written.update(path=path, benchmark=benchmark, config=config, reports=reports)
        written.update(base_url=base_url, tool_files=tool_files, extra=extra)
        written.update(run_start=run_start)

    monkeypatch.setattr(bench_cli, "write_report", write_report)
    good = {
        CASE[n]["prompt"]: pair[0]
        for n, pair in GOOD_BAD.items()
        if n.startswith("json")
    }

    def answer(request):
        prompt = request["messages"][-1]["content"]
        return body(good.get(prompt, "ok") if request["model"] == "q" else "no")

    transport.answer = staticmethod(answer)
    return written


class TestMain:
    def test_a_category_run_writes_the_envelope(self, wired, capsys):
        assert (
            bc.main(["--category", "json", "--no-warmup", "--output", "chat.json"]) == 0
        )
        assert wired["benchmark"] == "bench_chat"
        assert wired["config"]["categories"] == ["json"]
        assert wired["config"]["cases"] == len(bc.JSON_CASES)
        assert wired["config"]["repeat_spacer"] is True
        assert set(wired["config"]["backend_entry"]) == {"npu", "cpu"}
        assert wired["tool_files"] == bc.TOOL_FILES
        assert wired["extra"]["determinism_probe"]["requests"] == 3
        assert "source_changed_during_run" not in wired["extra"]
        npu, cpu = wired["reports"]
        assert (npu["passed"], npu["total"]) == (8, 8)
        assert cpu["passed"] == 0
        ranking = capsys.readouterr().out.split("RANKING", 1)[1]
        assert ranking.index("npu") < ranking.index("cpu")

    def test_the_fingerprint_is_this_file_and_the_probe(self):
        # The determinism probe's verdict sets bench_compare's strict mode.
        own = os.path.abspath(bc.__file__)
        assert (own, "determinism.py") == bc.TOOL_FILES

    def test_the_run_start_record_reaches_the_report(self, wired, host_load):
        # write_report compares its start hash (a mid-run edit is named) and
        # records its load; the lane measured is the first candidate's.
        bc.main(["--category", "multiturn", "--no-warmup", "--output", "chat.json"])
        start = wired["run_start"]
        assert start["tool_files"] == list(bc.TOOL_FILES)
        assert start["host_load"]["other_cores"] == 0.2
        assert host_load == [{"seconds": 3, "lane": CANDIDATES[0]["base_url"]}]

    def test_no_output_writes_nothing(self, wired):
        bc.main(["--category", "multiturn", "--no-warmup"])
        assert wired == {}

    def test_suspect_cases_leave_the_categories_in_this_tools_shape(
        self, wired, monkeypatch, transport
    ):
        control = dict(CANDIDATES[0], label="control", backend="control", model="c")
        monkeypatch.setattr(
            bench_cli, "candidate_rows", lambda *a, **k: [control, dict(CANDIDATES[0])]
        )
        good = {CASE[n]["prompt"]: GOOD_BAD[n][0] for n in GOOD_BAD if "json" in n}
        people = CASE["json_people"]["prompt"]

        def answer(request):
            prompt = request["messages"][-1]["content"]
            if request["model"] == "c" and prompt == people:
                return body("no")  # the control fails one case: it is suspect
            return body(good.get(prompt, "ok"))

        transport.answer = staticmethod(answer)
        bc.main(["--category", "json", "--no-warmup", "--output", "chat.json"])
        _, npu = wired["reports"]
        assert npu["suspect_cases"] == ["json_people"]
        assert (npu["passed"], npu["total"]) == (7, 7)
        # Re-derived without the suspect case, and still with the rows nobody
        # graded and the per-case counts, not bench_compare's bare pair.
        assert npu["categories"]["json"] == {
            "passed": 7, "total": 7, "excluded": 0, "cases_passed": 7, "cases": 7,
        }  # fmt: skip
