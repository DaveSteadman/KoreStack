from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from KoreAgentNetwork.app import runtime, store
from KoreAgentNetwork.app.server import app


class KoreAgentNetworkTests(unittest.TestCase):
    def test_network_rejects_duplicate_input_connection(self) -> None:
        network = store.default_network()
        network["edges"].append({
            "id":          "edge_duplicate",
            "source_node": "agent_status",
            "source_port": "model",
            "target_node": "status_summary",
            "target_port": "status",
        })
        with self.assertRaisesRegex(ValueError, "more than one connection"):
            store.validate_network(network)

    def test_runtime_passes_output_to_named_input(self) -> None:
        network = {
            "id": "test_network",
            "nodes": [
                {"id": "source", "label": "Source", "position": {}, "inputs": [], "outputs": [{"name": "value"}], "code": "outputs['value'] = 4"},
                {"id": "target", "label": "Target", "position": {}, "inputs": [{"name": "value"}], "outputs": [{"name": "doubled"}], "code": "outputs['doubled'] = inputs['value'] * 2"},
            ],
            "edges": [{"id": "edge_value", "source_node": "source", "source_port": "value", "target_node": "target", "target_port": "value"}],
        }
        clean = store.validate_network(network)
        with patch.object(runtime, "suite_services", return_value={}):
            result = runtime.run_network(clean)
        self.assertTrue(result["ok"])
        self.assertEqual(result["order"], ["source", "target"])
        self.assertEqual(result["nodes"]["target"]["outputs"]["doubled"], 8)
        self.assertIn("inputs", result["nodes"]["target"])

    def test_runtime_rejects_cycles(self) -> None:
        network = {
            "id": "cycle_network",
            "nodes": [
                {"id": "one", "label": "One", "position": {}, "inputs": [{"name": "value"}], "outputs": [{"name": "value"}], "code": "outputs['value'] = inputs['value']"},
                {"id": "two", "label": "Two", "position": {}, "inputs": [{"name": "value"}], "outputs": [{"name": "value"}], "code": "outputs['value'] = inputs['value']"},
            ],
            "edges": [
                {"id": "edge_one", "source_node": "one", "source_port": "value", "target_node": "two", "target_port": "value"},
                {"id": "edge_two", "source_node": "two", "source_port": "value", "target_node": "one", "target_port": "value"},
            ],
        }
        clean = store.validate_network(network)
        with self.assertRaisesRegex(ValueError, "cycle"):
            runtime.run_network(clean)

    def test_network_api_creates_saves_and_runs_a_network(self) -> None:
        with TemporaryDirectory() as directory, patch.object(store, "NETWORKS_DIR", Path(directory)):
            client   = TestClient(app)
            created  = client.post("/api/networks", json={"title": "API network"})
            self.assertEqual(created.status_code, 200)
            network  = created.json()["network"]
            network["nodes"] = [{
                "id":       "constant",
                "label":    "Constant",
                "position": {"x": 0, "y": 0},
                "inputs":   [],
                "outputs":  [{"name": "answer"}],
                "code":     "outputs['answer'] = 42",
            }]
            network["edges"] = []
            saved = client.put(f"/api/networks/{network['id']}", json={"network": network})
            self.assertEqual(saved.status_code, 200)
            run = client.post(f"/api/networks/{network['id']}/run")
            self.assertEqual(run.status_code, 200)
            self.assertEqual(run.json()["run"]["nodes"]["constant"]["outputs"]["answer"], 42)

    def test_single_block_runs_with_custom_inputs(self) -> None:
        node = {"id": "n", "code": "outputs['out'] = inputs['x'] * 2", "inputs": [{"name": "x"}], "outputs": [{"name": "out"}]}
        ok = runtime.run_node(node, {"x": 21})
        self.assertEqual((ok["status"], ok["outputs"]["out"]), ("completed", 42))
        bad = runtime.run_node({**node, "code": "pass"}, {"x": 1})
        self.assertEqual(bad["status"], "failed")
        response = TestClient(app).post("/api/run-node", json={"node": node, "inputs": {"x": 5}})
        self.assertEqual(response.json()["result"]["outputs"]["out"], 10)

    def test_llm_block_renders_prompt_template(self) -> None:
        node = {
            "id": "ask",
            "kind": "llm",
            "config": {"prompt_template": "Summarise {topic}"},
            "inputs": [{"name": "topic"}],
            "outputs": [{"name": "response"}, {"name": "prompt"}, {"name": "model"}],
            "code": "",
        }
        with patch.object(runtime, "_call_llm", return_value={"response": "Done", "model": "chat-x", "prompt_tokens": 12, "completion_tokens": 5, "tokens_per_second": 20.0}) as llm:
            result = runtime.run_node(node, {"topic": "the outage"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["response"], "Done")
        self.assertEqual(result["outputs"]["prompt"], "Summarise the outage")
        self.assertEqual(result["outputs"]["model"], "chat-x")
        self.assertEqual(llm.call_args.args[:2], ("Summarise the outage", ""))

    def test_judge_block_converts_probability_to_verdict(self) -> None:
        node = {
            "id": "judge",
            "kind": "judge",
            "config": {"question": "Does {response} answer {prompt}?", "threshold": 0.8},
            "inputs": [{"name": "prompt"}, {"name": "response"}],
            "outputs": [{"name": "verdict"}, {"name": "probability"}, {"name": "question"}],
            "code": "",
        }
        with patch.object(runtime, "_call_decision", return_value={"probability": 0.92, "model": "clef"}) as judge:
            result = runtime.run_node(node, {"prompt": "Capital of France?", "response": "Paris"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["verdict"], True)
        self.assertEqual(result["outputs"]["probability"], 0.92)
        self.assertEqual(result["outputs"]["question"], "Does Paris answer Capital of France??")
        self.assertEqual(judge.call_args.args[1], {"prompt": "Capital of France?", "response": "Paris"})

    def test_python_block_helpers_can_call_llm_and_judge(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                packet = json.loads(body["json_text"])
                if packet.get("route") == "system_one":
                    payload = {"response": json.dumps({"verdict": {"noul": 0.85}}), "model": "clef", "prompt_tokens": 1, "completion_tokens": 1, "tokens_per_second": 0}
                else:
                    payload = {"response": "stubbed response", "model": "chat", "prompt_tokens": 2, "completion_tokens": 3, "tokens_per_second": 4}
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):  # noqa: A003
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        node = {
            "id": "helpers",
            "kind": "python",
            "inputs": [{"name": "prompt"}, {"name": "response"}],
            "outputs": [{"name": "answer"}, {"name": "score"}],
            "code": "outputs['answer'] = llm(inputs['prompt'])\noutputs['score'] = judge('Does the response answer the prompt?', {'prompt': inputs['prompt'], 'response': inputs['response']})",
        }
        try:
            with patch.object(runtime, "suite_services", return_value={"koreagent": f"http://127.0.0.1:{server.server_port}"}):
                result = runtime.run_node(node, {"prompt": "Say hello", "response": "Hello"})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["answer"], "stubbed response")
        self.assertEqual(result["outputs"]["score"], 0.85)

    def test_python_block_file_helpers_use_datauser_root(self) -> None:
        with TemporaryDirectory() as root:
            code = (
                "write_json('idx.json', {'i': 2})\n"
                "outputs['data'] = read_json('idx.json')\n"
                "outputs['files'] = list_files('*.json')\n"
                "with open('sub/a.txt', 'w') as handle:\n"
                "    handle.write('hi')\n"
                "outputs['text'] = read_text('sub/a.txt')\n"
                "outputs['rel'] = exists('sub/a.txt')\n"
            )
            node = {"kind": "python", "code": code, "inputs": [], "outputs": [{"name": n} for n in ("data", "files", "text", "rel")]}
            bad = {**node, "code": "read_text('../secret.txt')", "outputs": []}
            with patch("KoreAgentNetwork.app.runtime.get_datauser_root", return_value=Path(root)):
                result = runtime.run_node(node, {})
                escaped = runtime.run_node(bad, {})
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["outputs"], {"data": {"i": 2}, "files": ["idx.json"], "text": "hi", "rel": True})
            self.assertTrue((Path(root) / "sub" / "a.txt").exists())
            self.assertEqual(escaped["status"], "failed")
            self.assertIn("escapes", escaped["error"])


    def test_python_block_supports_early_return_and_novalue(self) -> None:
        code = (
            "outputs['result'] = NoValue\n"
            "if not exists('prompts.txt'):\n"
            "    return\n"
            "items = read_text('prompts.txt').splitlines()\n"
            "i = read_json('prompts.idx.json', default=0)\n"
            "outputs['result'] = items[i % len(items)]\n"
            "write_json('prompts.idx.json', (i + 1) % len(items))\n"
        )
        node = {"kind": "python", "code": code, "inputs": [], "outputs": [{"name": "result"}]}
        with TemporaryDirectory() as root, patch("KoreAgentNetwork.app.runtime.get_datauser_root", return_value=Path(root)):
            self.assertIsNone(runtime.run_node(node, {})["outputs"]["result"])
            (Path(root) / "prompts.txt").write_text("a\nb\n", encoding="utf-8")
            picked = [runtime.run_node(node, {})["outputs"]["result"] for _ in range(3)]
        self.assertEqual(picked, ["a", "b", "a"])


if __name__ == "__main__":    unittest.main()