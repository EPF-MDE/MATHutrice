"""Production signals must not export a secret carried in LLM_BASE_URL's path,
and must report the LLM failures the chat catches.

Run with: uv run python -m unittest discover tests
"""

import gzip
import json
import logging
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from fastapi.testclient import TestClient
from openai import OpenAI
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

TOKEN = "s3cr3t-path-token"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class _ChatCompletionStub(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _FailingLLMStub(BaseHTTPRequestHandler):
    """Answers every call with the provider's 500, in Mistral's shape."""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(
            {
                "object": "error",
                "message": "Internal server error",
                "type": "internal_server_error",
                "param": None,
                "code": None,
            }
        ).encode()
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _PostHogStub(BaseHTTPRequestHandler):
    """Records the events it receives, as PostHog's ingestion host would."""

    events = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        self.events.extend(json.loads(body).get("batch", []))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# The application's own LLM client is built at import time, from LLM_BASE_URL,
# so the stub it calls runs before mathutrice.app is imported.
_llm = _serve(_FailingLLMStub)

# mathutrice.app reads its settings at import time, so they are set first. The
# database is a file: the chat answers from a worker thread, and an in-memory
# SQLite database is empty in every thread but the one that created it.
os.environ.update(
    {
        "SESSION_SECRET": "test-session-secret",
        "AUTH_MODE": "dev",
        "DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/mathutrice.db",
        "LLM_BASE_URL": f"http://127.0.0.1:{_llm.server_port}/v1",
        "LLM_API_KEY": "k",
        "LLM_MODEL": "m",
        "POSTHOG_PROJECT_TOKEN": "phc_test",
        "POSTHOG_HOST": "http://posthog.invalid",
        "POSTHOG_ENVIRONMENT": "test",
    }
)

from mathutrice import app  # noqa: E402

app.create_db_and_tables()
app.seed_missing(app.engine)

EMAIL = "eleve@epf.fr"
POSTHOG_UNSET = {
    "POSTHOG_PROJECT_TOKEN": "",
    "POSTHOG_HOST": "",
    "POSTHOG_ENVIRONMENT": "",
}


def _ask_the_chat(question):
    """Signs in, asks the chat one question, returns its answer as streamed.

    The lifespan does not run, so the test's own PostHog client stays the app's.
    """
    browser = TestClient(app.app)
    browser.post("/dev/login", data={"email": EMAIL})
    response = browser.post("/chat/stream", json={"message": question})

    chunks = [
        line.removeprefix("data: ")
        for line in response.text.split("\n\n")
        if line.startswith("data: ")
    ]
    return "".join(c for c in chunks if not c.startswith(("[CONV_ID:", "[DONE]")))


class ProductionSignalsLogExport(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        self.addCleanup(setattr, root, "handlers", list(root.handlers))
        # As in production, where an import-time basicConfig sets it.
        root.setLevel(logging.INFO)

        self.exported = InMemoryLogRecordExporter()
        with mock.patch.object(app, "OTLPLogExporter", return_value=self.exported):
            client, self.provider = app.start_production_signals()
        self.addCleanup(client.shutdown)
        self.addCleanup(self.provider.shutdown)

        self.reached_root = _Capture()
        root.addHandler(self.reached_root)

    def exported_bodies(self):
        self.provider.force_flush()
        return [str(log.log_record.body) for log in self.exported.get_finished_logs()]

    def test_an_llm_call_exports_no_record_containing_the_path_token(self):
        # A real request through the client's own transport, whatever it is,
        # so the test still holds if openai changes its HTTP library.
        stub = ThreadingHTTPServer(("127.0.0.1", 0), _ChatCompletionStub)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        llm = OpenAI(
            base_url=f"http://127.0.0.1:{stub.server_port}/mathutrice/{TOKEN}/v1",
            api_key="k",
        )

        llm.chat.completions.create(
            model="m", messages=[{"role": "user", "content": "hi"}]
        )

        # The token does reach the root logger, so the test is not vacuous.
        self.assertTrue(
            any(TOKEN in r.getMessage() for r in self.reached_root.records)
        )
        self.assertFalse([b for b in self.exported_bodies() if TOKEN in b])

    def test_application_records_are_still_exported(self):
        logging.getLogger("mathutrice.app").info("application record")

        self.assertIn("application record", self.exported_bodies())


class CaughtLLMFailureReport(unittest.TestCase):
    def setUp(self):
        _PostHogStub.events = []
        posthog = _serve(_PostHogStub)
        self.addCleanup(posthog.server_close)
        self.addCleanup(posthog.shutdown)
        self.posthog_host = f"http://127.0.0.1:{posthog.server_port}"

    def start_signals(self, settings):
        """Starts production signals as the lifespan does, with these settings."""
        with mock.patch.dict(os.environ, settings):
            client, provider = app.start_production_signals()
        if provider:
            self.addCleanup(provider.shutdown)
        patch = mock.patch.object(app, "posthog_client", client)
        patch.start()
        self.addCleanup(patch.stop)
        return client

    def test_a_caught_provider_failure_is_reported_with_the_users_identity(self):
        client = self.start_signals({"POSTHOG_HOST": self.posthog_host})

        answer = _ask_the_chat("Combien font 2 + 2 ?")
        client.shutdown()

        self.assertTrue(answer.startswith("Erreur: Error code: 500"), answer)
        exceptions = [e for e in _PostHogStub.events if e["event"] == "$exception"]
        self.assertEqual(len(exceptions), 1, _PostHogStub.events)
        self.assertEqual(exceptions[0]["distinct_id"], EMAIL)
        self.assertEqual(exceptions[0]["properties"]["environment"], "test")
        self.assertEqual(
            exceptions[0]["properties"]["$exception_list"][0]["type"],
            "InternalServerError",
        )

    def test_the_answer_is_the_same_with_production_signals_on_or_off(self):
        self.assertIsNone(self.start_signals(POSTHOG_UNSET))
        answer_off = _ask_the_chat("Combien font 2 + 2 ?")

        client = self.start_signals({"POSTHOG_HOST": self.posthog_host})
        answer_on = _ask_the_chat("Combien font 2 + 2 ?")
        client.shutdown()

        self.assertEqual(answer_on, answer_off)

    def test_with_the_posthog_settings_unset_nothing_is_sent(self):
        self.assertIsNone(self.start_signals(POSTHOG_UNSET))

        answer = _ask_the_chat("Combien font 2 + 2 ?")

        self.assertTrue(answer.startswith("Erreur: Error code: 500"), answer)
        self.assertEqual(_PostHogStub.events, [])


if __name__ == "__main__":
    unittest.main()
