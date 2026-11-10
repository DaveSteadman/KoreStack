from __future__ import annotations

import json
import re
import unittest
from html import unescape
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from KoreCommon import suite_paths
from KoreData.KoreTex.app.processor import (
    DEDUPLICATION_FORMAT, DEDUPLICATION_PROMPT, EXPANSION_FORMAT, EXPANSION_PROMPT,
    MAX_TEXT_LENGTH, SYSTEM_PROMPT, TextProcessor,
)
from KoreData.KoreTex.app.server import app, get_processor


CHAT_MODEL = "nemotron-3.5-lightning:latest"
SOURCE_TEXT = "The library opens at 9am on Monday."
RESULT = {
    "statements": [
        {"statement": "The library opens at 9am on Monday.", "source_excerpt": SOURCE_TEXT},
    ],
}


class KoreTexTests(unittest.TestCase):
    def setUp(self):
        self.agent = {"model": CHAT_MODEL, "host": "http://localhost:11434"}
        self.loaded = {"models": [{"name": CHAT_MODEL}]}
        self.reply = {"done": True, "message": {"content": json.dumps(RESULT)}}
        self.failure = ""
        self.requests = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle_request))
        processor = TextProcessor(self.http, "http://127.0.0.1:29601")
        app.dependency_overrides[get_processor] = lambda: processor
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.client.portal.call(self.http.aclose)
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    def handle_request(self, request):
        self.requests.append(request)
        path = request.url.path
        if self.failure == path:
            raise httpx.ConnectError("Unavailable", request=request)
        if path == "/api/status":
            return httpx.Response(200, json=self.agent)
        if path == "/api/ps":
            return httpx.Response(200, json=self.loaded)
        if path == "/api/chat":
            if self.failure == "timeout":
                raise httpx.ReadTimeout("Timeout", request=request)
            if self.failure == "http":
                return httpx.Response(500, json={"error": "failed"})
            return httpx.Response(200, json=self.reply)
        self.fail(f"Unexpected request: {request.method} {request.url}")

    def assert_no_generation(self):
        self.assertFalse(any(request.method == "POST" for request in self.requests))

    def test_preview_uses_current_configured_model_and_json_mode(self):
        response = self.client.post("/api/process", json={"text": SOURCE_TEXT})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"model": CHAT_MODEL, "result": RESULT}, response.json())
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(CHAT_MODEL, payload["model"])
        self.assertEqual("json", payload["format"])
        self.assertFalse(payload["stream"])
        self.assertEqual({"role": "system", "content": SYSTEM_PROMPT}, payload["messages"][0])
        self.assertEqual(SOURCE_TEXT, payload["messages"][-1]["content"])

    def test_custom_system_prompt_is_used_only_for_its_request(self):
        prompt = "Extract atomic factual statements with verbatim source excerpts. Return JSON only."
        result = {"statements": [{"statement": "The input says Test.", "source_excerpt": "Test"}]}
        self.reply = {"done": True, "message": {"content": json.dumps(result)}}
        response = self.client.post("/api/process", json={"text": "Test", "system_prompt": prompt})
        self.assertEqual(200, response.status_code)
        self.assertEqual(result, response.json()["result"])
        payload = json.loads(self.requests[-1].content)
        self.assertEqual({"role": "system", "content": prompt}, payload["messages"][0])
        self.assertEqual("json", payload["format"])
        self.reply = {"done": True, "message": {"content": json.dumps(RESULT)}}
        self.client.post("/api/process", json={"text": SOURCE_TEXT})
        self.assertEqual(SYSTEM_PROMPT, json.loads(self.requests[-1].content)["messages"][0]["content"])

    def test_invalid_system_prompt_never_calls_model(self):
        for prompt in ("", " \n ", None, 12, "a" * (MAX_TEXT_LENGTH + 1)):
            with self.subTest(prompt_type=type(prompt)):
                response = self.client.post("/api/process", json={"text": "Test", "system_prompt": prompt})
                self.assertEqual(422, response.status_code)
        self.assertEqual([], self.requests)

    def test_system_prompt_length_boundary(self):
        prompt = "a" * MAX_TEXT_LENGTH
        response = self.client.post("/api/process", json={"text": SOURCE_TEXT, "system_prompt": prompt})
        self.assertEqual(200, response.status_code)
        self.assertEqual(prompt, json.loads(self.requests[-1].content)["messages"][0]["content"])

    def test_ready_requires_loaded_chat_model_not_just_any_model(self):
        self.loaded = {"models": [{"name": "clef:latest"}]}
        state = self.client.get("/api/readiness").json()
        self.assertFalse(state["ready"])
        self.assertIn("not loaded", state["reason"])
        response = self.client.post("/api/process", json={"text": "Test snippet"})
        self.assertEqual(503, response.status_code)
        self.assert_no_generation()

    def test_readiness_does_not_load_or_generate(self):
        state = self.client.get("/api/readiness").json()
        self.assertTrue(state["ready"])
        self.assertEqual(CHAT_MODEL, state["model"])
        self.assertNotIn("host", state)
        self.assert_no_generation()

    def test_missing_default_tag_matches_loaded_latest(self):
        self.agent["model"] = CHAT_MODEL.removesuffix(":latest")
        self.assertTrue(self.client.get("/api/readiness").json()["ready"])

    def test_unloaded_model_is_rechecked_after_ready(self):
        self.assertTrue(self.client.get("/api/readiness").json()["ready"])
        self.loaded = {"models": []}
        self.assertEqual(503, self.client.post("/api/process", json={"text": "Test"}).status_code)
        self.assert_no_generation()

    def test_changed_agent_model_is_not_cached(self):
        self.assertTrue(self.client.get("/api/readiness").json()["ready"])
        self.agent["model"] = "clef:latest"
        self.assertEqual(503, self.client.post("/api/process", json={"text": "Test"}).status_code)
        self.assert_no_generation()

    def test_ollama_and_agent_unavailable(self):
        for failure in ("/api/status", "/api/ps"):
            with self.subTest(failure=failure):
                self.failure = failure
                self.assertFalse(self.client.get("/api/readiness").json()["ready"])
                self.assertEqual(503, self.client.post("/api/process", json={"text": "Test"}).status_code)
        self.assert_no_generation()

    def test_missing_model_and_invalid_model_list_fail_closed(self):
        for agent, loaded in (
            ({}, self.loaded),
            ({**self.agent, "model": ""}, self.loaded),
            (self.agent, {}),
            (self.agent, {"models": [None]}),
        ):
            with self.subTest(agent=agent, loaded=loaded):
                self.agent, self.loaded = agent, loaded
                self.assertFalse(self.client.get("/api/readiness").json()["ready"])
        self.assert_no_generation()

    def test_external_host_is_rejected_without_sending_text(self):
        self.agent["host"] = "https://example.com"
        self.assertEqual(503, self.client.post("/api/process", json={"text": "Private text"}).status_code)
        self.assertTrue(all(request.url.host == "127.0.0.1" for request in self.requests))

    def test_invalid_input_never_calls_model(self):
        for body in ({}, {"text": ""}, {"text": " \n "}, {"text": 12},
                     {"text": "a" * (MAX_TEXT_LENGTH + 1)}, {"text": "Test", "model": CHAT_MODEL}):
            with self.subTest(body_type=type(body.get("text"))):
                self.assertEqual(422, self.client.post("/api/process", json=body).status_code)
        self.assertEqual([], self.requests)

    def test_input_length_boundary(self):
        self.reply = {"done": True, "message": {"content": json.dumps({"statements": [
            {"statement": "The source contains repeated a characters.", "source_excerpt": "aaaa"},
        ]})}}
        response = self.client.post("/api/process", json={"text": "a" * MAX_TEXT_LENGTH})
        self.assertEqual(200, response.status_code)

    def test_flat_statements_preserve_attribution_and_source_context(self):
        source_text = "Aster Labs reported 12 tests. The company plans to publish results."
        examples = [
            {"statements": [
                {"statement": "Aster Labs reported 12 tests.", "source_excerpt": "Aster Labs reported 12 tests."},
                {"statement": "Aster Labs plans to publish results.",
                 "source_excerpt": "The company plans to publish results."},
            ]},
            {"statements": []},
        ]
        for result in examples:
            with self.subTest(result=result):
                self.reply = {"done": True, "message": {"content": json.dumps(result)}}
                response = self.client.post("/api/process", json={"text": source_text})
                self.assertEqual(200, response.status_code)
                self.assertEqual(result, response.json()["result"])

    def test_excerpt_must_appear_in_the_source(self):
        self.reply = {"done": True, "message": {"content": json.dumps(RESULT)}}
        response = self.client.post("/api/process", json={"text": "A different source."})
        self.assertEqual(502, response.status_code)

    def test_expansion_sends_one_statement_with_full_original_text(self):
        source_text = "Aster Labs completed 12 tests in London. The tests required a safety review."
        original = {"statement": "Aster Labs completed 12 tests in London.",
                    "source_excerpt": "Aster Labs completed 12 tests in London."}
        expanded = {"statements": [
            original,
            {"statement": "The tests required a safety review.",
             "source_excerpt": "The tests required a safety review."},
        ]}
        self.reply = {"done": True, "message": {"content": json.dumps(expanded)}}

        response = self.client.post("/api/expand-statement", json={"text": source_text, "statement": original})

        self.assertEqual(200, response.status_code)
        self.assertEqual({"model": CHAT_MODEL, "result": expanded, "warnings": []}, response.json())
        chat_requests = [request for request in self.requests if request.url.path == "/api/chat"]
        self.assertEqual(1, len(chat_requests))
        payload = json.loads(chat_requests[0].content)
        self.assertEqual(EXPANSION_FORMAT, payload["format"])
        self.assertEqual(EXPANSION_PROMPT, payload["messages"][0]["content"])
        self.assertEqual({"source_text": source_text, "statement_to_expand": original},
                         json.loads(payload["messages"][1]["content"]))

    def test_expansion_rejects_unsourced_input_and_retains_original_for_unsourced_output(self):
        source_text = "The tests required a safety review."
        original = {"statement": "A safety review was required.",
                    "source_excerpt": "The tests required a safety review."}
        unsourced = {**original, "source_excerpt": "No such passage."}
        response = self.client.post("/api/expand-statement", json={"text": source_text, "statement": unsourced})
        self.assertEqual(422, response.status_code)
        self.assert_no_generation()

        self.reply = {"done": True, "message": {"content": json.dumps({"statements": [
            {"statement": "An unsupported consequence.", "source_excerpt": "The project was cancelled."},
        ]})}}
        response = self.client.post("/api/expand-statement", json={"text": source_text, "statement": original})
        self.assertEqual(200, response.status_code)
        self.assertEqual([original], response.json()["result"]["statements"])
        self.assertEqual(2, len(response.json()["warnings"]))

    def test_expansion_keeps_sourced_items_when_one_excerpt_is_invalid(self):
        original = {"statement": "The library opens at 9am on Monday.", "source_excerpt": SOURCE_TEXT}
        self.reply = {"done": True, "message": {"content": json.dumps({"statements": [
            original,
            {"statement": "The library closes at 5pm.", "source_excerpt": "Closes at 5pm."},
        ]})}}
        response = self.client.post("/api/expand-statement", json={"text": SOURCE_TEXT, "statement": original})
        self.assertEqual(200, response.status_code)
        self.assertEqual([original], response.json()["result"]["statements"])
        self.assertIn("1 unsourced", response.json()["warnings"][0])

    def test_expansion_requires_one_or_more_statements(self):
        original = {"statement": "The library opens at 9am on Monday.", "source_excerpt": SOURCE_TEXT}
        self.reply = {"done": True, "message": {"content": '{"statements": []}'}}
        response = self.client.post("/api/expand-statement", json={"text": SOURCE_TEXT, "statement": original})
        self.assertEqual(200, response.status_code)
        self.assertEqual([original], response.json()["result"]["statements"])
        self.assertTrue(response.json()["warnings"])

    def test_expansion_malformed_model_output_retains_original(self):
        original = {"statement": "The library opens at 9am on Monday.", "source_excerpt": SOURCE_TEXT}
        self.reply = {"done": True, "message": {"content": '{"unusable": true}'}}
        response = self.client.post("/api/expand-statement", json={"text": SOURCE_TEXT, "statement": original})
        self.assertEqual(200, response.status_code)
        self.assertEqual([original], response.json()["result"]["statements"])
        self.assertIn("could not be parsed", response.json()["warnings"][0])
        self.assertEqual(1, sum(request.url.path == "/api/chat" for request in self.requests))

    def test_final_collation_deduplicates_claims_and_retains_all_evidence(self):
        text = "The library opens at 9am on Monday. On Monday, the library opens at 9am."
        first = {
            "statement": "The library opens at 9am at 9am on Monday.",
            "source_excerpt": "The library opens at 9am on Monday.",
            "expanded_from": ["The library opens at 9am on Monday."],
            "expanded_from_excerpts": ["The library opens at 9am on Monday."],
            "expanded_from_indices": [1],
        }
        second = {
            "statement": "On Monday, the library opens at 9am.",
            "source_excerpt": "On Monday, the library opens at 9am.",
            "expanded_from": ["On Monday, the library opens at 9am."],
            "expanded_from_excerpts": ["On Monday, the library opens at 9am."],
            "expanded_from_indices": [2],
        }
        deduplicated = {"statements": [
            {"statement": "The library opens at 9am on Monday.", "source_indices": [1, 2]},
        ]}
        self.reply = {"done": True, "message": {"content": json.dumps(deduplicated)}}

        response = self.client.post("/api/collate-statements", json={"text": text, "statements": [first, second]})

        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["warnings"])
        self.assertEqual([{
            "statement": "The library opens at 9am on Monday.",
            "source_excerpt": first["source_excerpt"],
            "source_excerpts": [first["source_excerpt"], second["source_excerpt"]],
            "expanded_from": first["expanded_from"] + second["expanded_from"],
            "expanded_from_excerpts": first["expanded_from_excerpts"] + second["expanded_from_excerpts"],
            "expanded_from_indices": [1, 2],
        }], response.json()["result"]["statements"])
        chat_requests = [request for request in self.requests if request.url.path == "/api/chat"]
        self.assertEqual(1, len(chat_requests))
        payload = json.loads(chat_requests[0].content)
        self.assertEqual(DEDUPLICATION_FORMAT, payload["format"])
        self.assertEqual(DEDUPLICATION_PROMPT, payload["messages"][0]["content"])
        self.assertEqual([{"index": 1, **first}, {"index": 2, **second}],
                         json.loads(payload["messages"][1]["content"])["statements"])

    def test_final_collation_retains_input_if_model_omits_a_claim(self):
        text = "The library opens at 9am on Monday. The library closes at 5pm."
        claims = ["The library opens at 9am on Monday.", "The library closes at 5pm."]
        statements = [
            {"statement": claim, "source_excerpt": claim, "expanded_from": [claim],
             "expanded_from_excerpts": [claim], "expanded_from_indices": [index]}
            for index, claim in enumerate(claims, 1)
        ]
        self.reply = {"done": True, "message": {"content": json.dumps({"statements": [
            {"statement": statements[0]["statement"], "source_indices": [1]},
        ]})}}

        response = self.client.post("/api/collate-statements", json={"text": text, "statements": statements})

        self.assertEqual(200, response.status_code)
        self.assertEqual(statements, response.json()["result"]["statements"])
        self.assertTrue(response.json()["warnings"])

    def test_final_collation_rejects_unsourced_input_without_generation(self):
        item = {"statement": "The library opens at 9am on Monday.",
                "source_excerpt": "A passage absent from the source.",
                "expanded_from": ["The library opens at 9am on Monday."],
                "expanded_from_excerpts": [SOURCE_TEXT], "expanded_from_indices": [1]}
        response = self.client.post("/api/collate-statements", json={"text": SOURCE_TEXT, "statements": [item]})
        self.assertEqual(422, response.status_code)
        self.assert_no_generation()

    def test_invalid_statement_shape_is_rejected(self):
        for result in ({}, {"statements": ["a fact"]},
                       {"statements": [{"statement": "A fact."}]},
                       {"statements": [{"statement": "A fact.", "source_excerpt": "A fact.", "topic": "other"}]}):
            with self.subTest(result=result):
                self.reply = {"done": True, "message": {"content": json.dumps(result)}}
                response = self.client.post("/api/process", json={"text": "A fact."})
                self.assertEqual(502, response.status_code)

    def test_invalid_or_incomplete_model_response_is_not_a_preview(self):
        for reply in (
            {"done": False, "message": {"content": json.dumps(RESULT)}},
            {"done": True, "error": "failed"},
            {"done": True, "message": {"content": "not JSON"}},
            {"done": True, "message": {"content": "[]"}},
            {"done": True, "message": {"content": "null"}},
            {"done": True, "message": {"content": '"plain text"'}},
            {"done": True, "message": {"content": "42"}},
            {"done": True, "message": {"content": "true"}},
            {"done": True, "message": {"content": '{"broken":'}},
            {"done": True, "message": {"content": '```json\n{"fact": "Test"}\n```'}},
            {"done": True, "message": {"content": '{"number": NaN}'}},
            {"done": True, "message": {"content": '{"number": Infinity}'}},
            {"done": True, "message": {"content": '{"nested": [{"number": -Infinity}]}'}},
            {"done": True, "message": {"content": '{"nested": {"number": 1e999}}'}},
        ):
            with self.subTest(reply=reply):
                self.reply = reply
                response = self.client.post("/api/process", json={"text": "Test"})
                self.assertEqual(502, response.status_code)
                self.assertNotIn("result", response.json())

    def test_ollama_request_errors_are_explicit(self):
        for failure, expected in (("/api/chat", 502), ("http", 502), ("timeout", 504)):
            with self.subTest(failure=failure):
                self.failure = failure
                self.assertEqual(expected, self.client.post("/api/process", json={"text": "Test"}).status_code)

    def test_page_and_shell_assets_are_available(self):
        page = self.client.get("/ui/tex")
        self.assertEqual(200, page.status_code)
        self.assertIn('type="submit" disabled>Process', page.text)
        self.assertIn("Preview only: nothing is saved.", page.text)
        self.assertIn('<details id="system-prompt-section">', page.text)
        self.assertIn("EVIDENCE-BASED STATEMENTS", page.text)
        self.assertIn("CONTEXT EXPANSION", page.text)
        self.assertIn('id="expand-button"', page.text)
        self.assertIn('id="expanded-result"', page.text)
        prompt = re.search(r'<textarea id="system-prompt"[^>]*>(.*?)</textarea>', page.text, re.DOTALL)
        self.assertIsNotNone(prompt)
        self.assertEqual(SYSTEM_PROMPT, unescape(prompt.group(1)))
        expanded_prompt = re.search(r'<textarea id="expansion-prompt"[^>]*>(.*?)</textarea>', page.text, re.DOTALL)
        self.assertIsNotNone(expanded_prompt)
        self.assertEqual(EXPANSION_PROMPT, unescape(expanded_prompt.group(1)))
        self.assertEqual(200, self.client.get("/static/tex/tex.js").status_code)
        self.assertEqual(200, self.client.get("/ui-elements/assets/icons/Gentlecons/brain-svgrepo-com.svg").status_code)
        self.assertEqual("preview", self.client.get("/status").json()["mode"])

    def test_default_system_prompt_is_html_escaped(self):
        prompt = 'Extract <facts> & ignore </textarea><script>alert("test")</script>.'
        with patch("KoreData.KoreTex.app.server.SYSTEM_PROMPT", prompt):
            page = self.client.get("/ui/tex")
        rendered = re.search(r'<textarea id="system-prompt"[^>]*>(.*?)</textarea>', page.text, re.DOTALL)
        self.assertIsNotNone(rendered)
        self.assertEqual(prompt, unescape(rendered.group(1)))
        self.assertNotIn('<script>alert("test")</script>', page.text)

    def test_url_map_preserves_overrides_and_adds_new_services(self):
        config = {"network": {"host": "127.0.0.1"}, "services": {
            "koreagent": {"port": 29601}, "koretex": {"port": 29620},
        }}
        with patch.object(suite_paths, "load_suite_config", return_value=config), patch.dict(
            "os.environ", {"KORE_SUITE_URLS": '{"koreagent": "http://localhost:29601"}'},
        ):
            urls = suite_paths.get_suite_urls_map()
        self.assertEqual("http://localhost:29601", urls["koreagent"])
        self.assertEqual("http://127.0.0.1:29620/", urls["koretex"])
