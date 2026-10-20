"""No OPS login, AI endpoint, Docker, or deployment required."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_llm():
    spec = importlib.util.spec_from_file_location("truchat_llm", ROOT / "packages/v1/chat/llm.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=object)}):
        spec.loader.exec_module(module)
    llm = object.__new__(module.LLM)
    llm.rate = 0
    return llm


class StreamerCompatibility(unittest.TestCase):
    def capture(self, token=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        received, errors = [], []

        def read():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    chunks = []
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    received.append(b"".join(chunks))
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        args = {"STREAM_HOST": "127.0.0.1", "STREAM_PORT": str(listener.getsockname()[1])}
        if token is not None:
            args["STREAM_TOKEN"] = token
        try:
            result = load_llm().stream(args, iter(["Hello", " città 🙂", "\nlast"]))
            thread.join(4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result, "Hello città 🙂\nlast")
            return received[0]
        finally:
            listener.close()

    def test_legacy_server_receives_only_newline_delimited_json(self):
        wire = self.capture()
        self.assertFalse(wire.startswith(b"OPS-STREAM "))
        self.assertTrue(wire.endswith(b"\n"))
        self.assertEqual([json.loads(line) for line in wire.splitlines()], ["Hello", " città 🙂", "\nlast"])

    def test_patched_server_receives_exact_preamble_before_records(self):
        token = "a" * 64
        wire = self.capture(token)
        preamble, payload = wire.split(b"\n", 1)
        self.assertEqual(preamble, ("OPS-STREAM " + token).encode())
        self.assertNotIn(token.encode(), payload)
        self.assertEqual([json.loads(line) for line in payload.splitlines()], ["Hello", " città 🙂", "\nlast"])

    def test_handshake_sent_before_first_model_token(self):
        token = "b" * 64
        sent = []

        def model():
            self.assertEqual(sent, [("OPS-STREAM " + token + "\n").encode()])
            yield "first"

        with patch("socket.create_connection") as connect:
            connect.return_value.sendall.side_effect = sent.append
            self.assertEqual(load_llm().stream({"STREAM_HOST": "fixture", "STREAM_PORT": 1234, "STREAM_TOKEN": token}, model()), "first")
            connect.return_value.close.assert_called_once()

    def test_invalid_tokens_never_open_a_connection(self):
        for token in ["bad", "A" * 64, "a" * 64 + "\n", ["a" * 64]]:
            with self.subTest(token_type=type(token).__name__), patch("socket.create_connection") as connect:
                self.assertEqual(load_llm().stream({"STREAM_HOST": "fixture", "STREAM_PORT": 1234, "STREAM_TOKEN": token}, iter(["hello"])), "")
                connect.assert_not_called()

    def test_nonstreaming_invocation_preserves_output(self):
        with patch("socket.create_connection") as connect:
            self.assertEqual(load_llm().stream({}, iter(["one", "two"])), "onetwo")
            connect.assert_not_called()

    def test_model_failure_always_closes_socket(self):
        def broken():
            yield "first"
            raise RuntimeError("synthetic model failure")

        with patch("socket.create_connection") as connect:
            self.assertEqual(load_llm().stream({"STREAM_HOST": "fixture", "STREAM_PORT": 1234, "STREAM_TOKEN": "c" * 64}, broken()), "first")
            connect.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
