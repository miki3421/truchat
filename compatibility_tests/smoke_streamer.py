#!/usr/bin/env python3
"""Exercise the real Streamer image and TruChat producer without AI or K3s.

Creates its own Docker network/container, publishes HTTP only on loopback,
and removes only those newly created resources. Requires Docker and Python 3.
This is a transport smoke test, not an OpenWhisk authorization test.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from test_streamer import load_llm


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:12]
    name = "truchat-smoke-" + suffix
    network = name + "-net"
    upstream = None
    created_network = created_container = False
    failures = []

    class Controller(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/api/v1/web/synthetic/v1/chat":
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                chunks = {
                    "welcome": ["Welcome", " to TruChat\n"],
                    "chat": ["Città", " 🙂", "\nsecond line"],
                    "rag": ["- **User**: synthetic\n", "- **Assistant**: answer\n"],
                }[body["input"]]
                output = load_llm().stream(body, iter(chunks))
                if output != "".join(chunks):
                    raise AssertionError("producer lost output")
                payload = json.dumps({"output": output}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    # Completing the callback cancels the now-unused action
                    # HTTP response. The downstream stream is asserted below.
                    pass
            except Exception as exc:
                failures.append(type(exc).__name__)
                self.send_error(500)

        def log_message(self, *_args):
            pass

    try:
        docker("network", "create", network)
        created_network = True
        configuration = json.loads(docker("network", "inspect", network))[0]
        subnet = configuration["IPAM"]["Config"][0]["Subnet"]
        # Let Docker select a free subnet, then make it explicit. Newer Docker
        # versions reject static endpoint IPs on automatically configured IPAM.
        docker("network", "rm", network)
        created_network = False
        docker("network", "create", "--subnet", subnet, network)
        created_network = True
        configuration = json.loads(docker("network", "inspect", network))[0]
        gateway = configuration["IPAM"]["Config"][0]["Gateway"]
        address = str(ipaddress.ip_address(gateway) + 1)
        upstream = ThreadingHTTPServer((gateway, 0), Controller)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        docker("create", "--name", name, "--network", network, "--ip", address,
               "-p", "127.0.0.1::8080", "-e", "HTTP_SERVER_PORT=8080",
               "-e", "STREAMER_ADDR=" + address,
               "-e", f"OW_APIHOST=http://{gateway}:{upstream.server_port}", args.image)
        created_container = True
        docker("start", name)
        host_port = docker("port", name, "8080/tcp")
        origin = "http://" + host_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for attempt in range(30):
            try:
                with opener.open(origin + "/", timeout=1) as response:
                    assert response.status == 200
                break
            except (OSError, urllib.error.URLError):
                if attempt == 29:
                    raise
                time.sleep(0.1)
        expected = {
            "welcome": "Welcome to TruChat\n",
            "chat": "Città 🙂\nsecond line",
            "rag": "- **User**: synthetic\n- **Assistant**: answer\n",
        }
        checks = []
        for case, text in expected.items():
            request = urllib.request.Request(origin + "/web/synthetic/v1/chat",
                data=json.dumps({"input": case}).encode(),
                headers={"Content-Type": "application/json"})
            with opener.open(request, timeout=25) as response:
                wire = response.read(65537)
                assert response.status == 200
                assert response.headers.get_content_type() == "application/x-ndjson"
            assert b"OPS-STREAM " not in wire
            assert wire.endswith(b"\n")
            actual = "".join(json.loads(line) for line in wire.splitlines() if line.strip())
            assert actual == text, case
            checks.append({"scenario": case, "status": "PASS"})
        assert failures == [], failures
        print(json.dumps({"ok": True, "image": args.image,
            "scope": "real Streamer HTTP/TCP + actual TruChat LLM.stream; fake AI/controller",
            "checks": checks}, indent=2))
        return 0
    finally:
        if created_container:
            docker("rm", "-f", name)
        if upstream:
            upstream.shutdown()
            upstream.server_close()
        if created_network:
            docker("network", "rm", network)


if __name__ == "__main__":
    sys.exit(main())
