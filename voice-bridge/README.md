# Lotuspond live voice bridge

This isolated Node.js process streams raw PCM audio to Deepgram's live speech-to-text WebSocket and sends each timed interim or final transcript revision to a Lotuspond voice session. It does not evaluate the transcript. A provider failure, timeout, full queue, or ingest failure terminates the bridge and never fabricates a final segment or passing assessment.

## Operator contract

The bridge requires Node.js 22 or newer and two server-side credentials:

- `DEEPGRAM_API_KEY`: Deepgram API key, sent only as `Authorization: Token ...` during the WebSocket handshake.
- `LOTUSPOND_API_KEY`: Lotuspond key with the ingest scope, sent only to the configured Lotuspond API.

Optional configuration:

- `DEEPGRAM_ENDPOINT`: operator-approved `wss:` endpoint without embedded credentials, query, or fragment. Default: `wss://api.deepgram.com/v1/listen`.
- `LOTUSPOND_API_URL`: Lotuspond API origin or the `/api/v1` base. Remote URLs must use HTTPS; plaintext HTTP is restricted to loopback. Default: `http://127.0.0.1:8000`.
- `LOTUSPOND_NETWORK_MODE`: `connected` or `airgap`. Airgap mode exits before a socket is created.

Install the pinned dependency from this package's lockfile:

```sh
npm ci --ignore-scripts
```

Stream mono, signed 16-bit little-endian PCM at 16 kHz from standard input:

```sh
cat call.pcm | npm start -- \
  --session 12345678-1234-4234-8234-123456789abc \
  --source stdin \
  --channels 1 \
  --channel-map 0:user \
  --sample-rate 16000
```

Use `--source file --path /absolute/path/audio.pcm` for a local file or `--source device --path /dev/...` for a local device that exposes a readable PCM byte stream. The bridge does not invoke a shell or a capture command. Capture tools can pipe PCM to stdin when the operating system does not expose a readable device.

Stereo conversation audio must map both input channels explicitly, for example `--channels 2 --channel-map 0:user,1:agent`. The bridge uses Deepgram multichannel results and never infers a person's identity from diarization or transcript content.

## Data and failure behavior

- Audio is processed in 32,000-byte chunks, never written by the bridge, and retained only in bounded runtime buffers.
- Provider WebSocket compression is disabled, individual provider events are capped at 256,000 bytes, the event queue holds at most 64 messages, and audio sending stops if provider backpressure persists for five seconds.
- Provider connection, idle, ingest, and finalization waits are bounded. An overflow or timeout stops the bridge instead of dropping evidence.
- Deepgram `is_final` is the only finality signal. `speech_final` marks an utterance boundary and does not replace segment finality.
- Logs contain lifecycle result codes only. Audio bytes, transcripts, URLs, and keys are not logged.
- Transcript text is sent directly to Lotuspond and is not retained locally by default. A user-selected input file remains the user's source file.

## Verified provider protocol

Research was refreshed on 2026-09-21 from Deepgram's official documentation:

- [Live Audio WebSocket reference](https://developers.deepgram.com/reference/speech-to-text/listen-streaming) documents `wss://api.deepgram.com/v1/listen`, binary media, `Results`, `Finalize`, `CloseStream`, the authorization header, `start`, `duration`, `channel_index`, and `is_final`.
- [Endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results) distinguishes mutable interim results from stable `is_final: true` segments.
- [Multichannel transcription](https://developers.deepgram.com/docs/multichannel) documents the first `channel_index` value as the audio channel associated with a result.
- [Finalize](https://developers.deepgram.com/docs/finalize) documents flushing buffered audio with `{ "type": "Finalize" }` and notes that a finalization response is not guaranteed when no meaningful audio remains.
- [Custom endpoints](https://developers.deepgram.com/reference/custom-endpoints) documents replacing the streaming endpoint for self-hosted deployments.

The WebSocket and HTTP behavior is covered with local mocks. No Deepgram credential was supplied, no live audio was sent, and provider availability has not been observed from this workspace. The installed `ws` dependency is pinned to 8.21.3; `npm audit` reported zero known vulnerabilities on 2026-09-21.

Run local validation with:

```sh
npm run check
npm audit
```
