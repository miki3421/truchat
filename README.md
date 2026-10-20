# TruChat — Streamer compatibility fork

Personal fork: `miki3421/truchat`, branch `advanced`, based on upstream
`4b2af8656f555357f1922edb7f9f1311335793f7`. This is not an Apache release.

TruChat uses an OpenAI-compatible endpoint configured through the existing
`AI_BASE_URL`, `AI_API_KEY` and `AI_CHAT_MODEL` action parameters. Their values
are deployment-specific and must remain private.

This branch supports both legacy Streamer and the authenticated callback
protocol: send a per-invocation `STREAM_TOKEN` preamble when supplied, then
newline-delimited JSON strings. The frontend buffers incomplete records and
UTF-8 code points across HTTP chunks and reports malformed streams.

Install the updated action **before** replacing the shared Streamer image.
The token is injected by Streamer, never added to `.env`, action annotations,
frontend code, logs, or application responses. Other streaming applications
must also be upgraded before the shared server changes.

The patched server currently allows a 20-second callback start, a one-second
authentication preamble, 30 seconds without callback data, five minutes total,
1 MiB of request JSON, and 64 concurrent streams per process. The action's
existing 10-minute timeout does not extend those proxy limits. In particular,
a model taking over 30 seconds to emit its first token may exceed the idle
deadline even after its handshake succeeds. Do not claim unrestricted long
generation compatibility or add an unauthenticated server fallback.

## Checks without deployment or secrets

```sh
python3 -B -m unittest discover -s compatibility_tests -p 'test_*.py' -v
bun test compatibility_tests/chat-stream.test.ts
npm ci --ignore-scripts --no-audit --no-fund
npm run build
./node_modules/.bin/tsc -p tsconfig.app.json --noEmit
```

The Bun binary bundled with OPS can also run the parser tests, for example
`~/.ops/linux-amd64/bin/bun test compatibility_tests/chat-stream.test.ts`.
These checks do not use the historical `tests/conftest.py`, OPS credentials,
AI providers or testcontainers.

`compatibility_tests/smoke_streamer.py --image IMAGE` additionally exercises
the real Streamer container over HTTP/TCP with this fork's actual Python
producer and synthetic model output. It requires Docker, creates its own
network/container, publishes HTTP only on loopback, and removes only those
temporary resources. It does not prove OpenWhisk authorization or a full
application deployment.

See [the remote Codex instructions](docs/REMOTE_CODEX.md) for the exact image,
installation order, validation and rollback. The image still has two known
availability findings; these changes are not a complete security clearance.
