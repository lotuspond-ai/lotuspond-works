import { pathToFileURL } from 'node:url';

const MAX_INPUT_BYTES = 1024 * 1024;

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) throw new Error('input_limit');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function writeEnvelope(envelope) {
  await new Promise((resolve, reject) => {
    process.stdout.write(JSON.stringify(envelope), (error) => (error ? reject(error) : resolve()));
  });
}

const controller = new AbortController();
process.once('SIGTERM', () => controller.abort());
process.once('SIGINT', () => controller.abort());

try {
  const adapterPath = process.argv[2];
  if (!adapterPath) throw new Error('adapter_missing');
  const request = await readRequest();
  const module = await import(pathToFileURL(adapterPath).href);
  if (typeof module.evaluate !== 'function') throw new Error('adapter_invalid');
  const result = await module.evaluate(request.input, request.expected, {
    revision: request.revision,
    runId: request.runId,
    scenarioId: request.scenarioId,
    signal: controller.signal,
  });
  await writeEnvelope({ status: 'success', result });
} catch {
  await writeEnvelope({ status: 'failure' }).catch(() => {});
  process.exitCode = 1;
}
