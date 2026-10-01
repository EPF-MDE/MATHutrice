"""Production signals must not export a secret carried in LLM_BASE_URL's path.

Run with: uv run python -m unittest discover tests
"""

import json
import logging
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from openai import OpenAI
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

# mathutrice.app reads its settings at import time, so they are set first.
os.environ.update(
    {
        "SESSION_SECRET": "test-session-secret",
        "AUTH_MODE": "dev",
        "DATABASE_URL": "sqlite://",
        "POSTHOG_PROJECT_TOKEN": "phc_test",
        "POSTHOG_HOST": "http://posthog.invalid",
        "POSTHOG_ENVIRONMENT": "test",
    }
)

from mathutrice import app  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
