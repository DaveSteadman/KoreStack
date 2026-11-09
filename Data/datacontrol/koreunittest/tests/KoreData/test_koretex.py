from __future__ import annotations

import json
import re
import unittest
from html import unescape
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from KoreCommon import suite_paths
from KoreData.KoreTex.app.processor import MAX_TEXT_LENGTH, SYSTEM_PROMPT, TextProcessor
from KoreData.KoreTex.app.server import app, get_processor


CHAT_MODEL = "nemotron-3.5-lightning:latest"
RESULT = {
    "title": "Library hours",
    "summary": "The library opens on Monday.",
    "statements": ["The library opens at 9am on Monday."],
    "key_terms": ["library", "opening hours"],
    "entities": [],
    "topics": ["Opening hours"],
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
        response = self.client.post("/api/process", json={"text": "The library opens at 9am on Monday."})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"model": CHAT_MODEL, "result": RESULT}, response.json())
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(CHAT_MODEL, payload["model"])
        self.assertEqual("json", payload["format"])
        self.assertFalse(payload["stream"])
        self.assertEqual({"role": "system", "content": SYSTEM_PROMPT}, payload["messages"][0])
        self.assertEqual("The library opens at 9am on Monday.", payload["messages"][-1]["content"])

    def test_custom_system_prompt_is_used_only_for_its_request(self):
        prompt = "Extract a datestamp object with day and month from the text. Return JSON only."
        result = {"datestamp": {"day": 5, "month": 12}}
        self.reply = {"done": True, "message": {"content": json.dumps(result)}}
        response = self.client.post("/api/process", json={"text": "Test", "system_prompt": prompt})
        self.assertEqual(200, response.status_code)
        self.assertEqual(result, response.json()["result"])
        payload = json.loads(self.requests[-1].content)
        self.assertEqual({"role": "system", "content": prompt}, payload["messages"][0])
        self.assertEqual("json", payload["format"])
        self.client.post("/api/process", json={"text": "Test"})
        self.assertEqual(SYSTEM_PROMPT, json.loads(self.requests[-1].content)["messages"][0]["content"])

    def test_invalid_system_prompt_never_calls_model(self):
        for prompt in ("", " \n ", None, 12, "a" * (MAX_TEXT_LENGTH + 1)):
            with self.subTest(prompt_type=type(prompt)):
                response = self.client.post("/api/process", json={"text": "Test", "system_prompt": prompt})
                self.assertEqual(422, response.status_code)
        self.assertEqual([], self.requests)

    def test_system_prompt_length_boundary(self):
        prompt = "a" * MAX_TEXT_LENGTH
        response = self.client.post("/api/process", json={"text": "Test", "system_prompt": prompt})
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
        response = self.client.post("/api/process", json={"text": "a" * MAX_TEXT_LENGTH})
        self.assertEqual(200, response.status_code)

    def test_topic_specific_json_fields_and_value_types_are_preserved(self):
        examples = [
            {"datestamp": {"original": "5th December", "day": 5, "month": 12, "year": None},
             "meeting": {"people": ["ABC"], "organisation": "qwerty", "project": "quantum magnesium", "block": 4}},
            {"email": {"subject": "Delivery", "actions": [{"action": "Confirm arrival", "completed": False}]}},
            {"customer_relationship": {"customer": "Example", "contacts": [{"name": "ABC"}]},
             "order": {"quantity": 3, "unit_price": 12.5, "currency": "GBP"}},
            {"intel": {"claim": "An unconfirmed report", "confirmed": False, "source": None}},
            {},
        ]
        for result in examples:
            with self.subTest(result=result):
                self.reply = {"done": True, "message": {"content": json.dumps(result)}}
                response = self.client.post("/api/process", json={"text": "Test snippet"})
                self.assertEqual(200, response.status_code)
                self.assertEqual(result, response.json()["result"])

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
        prompt = re.search(r'<textarea id="system-prompt"[^>]*>(.*?)</textarea>', page.text, re.DOTALL)
        self.assertIsNotNone(prompt)
        self.assertEqual(SYSTEM_PROMPT, unescape(prompt.group(1)))
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
