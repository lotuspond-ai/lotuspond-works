# Voice evidence

Voice evaluation is an evidence workflow. It does not place calls, operate a telephony provider, or attest to a provider's delivery unless a separately implemented adapter supplies that evidence.

For each session retain only the data allowed by the customer's policy:

- scenario and agent revision;
- audio format and duration metadata;
- transcript provenance and language;
- interruption, latency, task-success, and safety observations;
- evaluator version and human review outcome;
- explicit missing or redacted evidence.

Obtain the required consent before recording or transcribing people. Define retention and deletion rules before a pilot starts. Avoid treating a transcript as a perfect representation of audio; evaluate audio-level behavior when timing, overlap, tone, or pronunciation matters.

Voice adapter contracts are still integration-specific. A configured endpoint is not a verified call path.


## Live PCM transcription bridge

The repository includes `voice-bridge/`, an isolated Node process that streams signed 16-bit PCM to Deepgram and forwards interim/final transcript revisions to Lotuspond. Install its lockfile with `npm --prefix voice-bridge ci --ignore-scripts`. Issue an ingest-scoped Lotuspond key, configure the server-side speech-provider key, and create a session first. `LOTUSPOND_API_URL` may be the API origin or the `/api/v1` base. Ingest and provider sockets ignore ambient proxies and keep TLS verification enabled.

For mono audio, a typical local command is:

```sh
npm run voice -- --session YOUR_SESSION_UUID --source file --path /absolute/path/call.pcm --channels 1 --channel-map 0:user --sample-rate 16000
```

Stereo needs an explicit mapping such as `0:user,1:agent`. The bridge never infers a person's role from their words. It bounds provider and ingestion buffers and timeouts, does not retain audio locally, and stops on provider/ingestion failure rather than fabricating a final transcript. It refuses cloud transcription in airgap mode.

The repository's `voice-bridge/README.md` lists credentials, protocol sources, failure semantics, and exact options. Tests use mocked provider sockets; no real audio/provider session has been validated. After the application has finalized all segments, close the session and queue its assessment. Audio-specific qualities still need an appropriate audio evaluator; transcript evidence alone cannot prove them.
