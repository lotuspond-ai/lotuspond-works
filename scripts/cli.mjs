import { writeFile } from 'node:fs/promises';
import { request } from './client.mjs';

const [command, target, file] = process.argv.slice(2);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
try {
  if (command === 'gate' && uuid.test(target || '')) {
    const decision = await request(`/releases/${target}/evaluate`, {});
    console.log(
      JSON.stringify(
        {
          verdict: decision.verdict,
          reasons: decision.reasons,
          evidenceSha256: decision.evidence_sha256,
        },
        null,
        2,
      ),
    );
    if (decision.verdict !== 'pass') process.exitCode = 2;
  } else if (command === 'export' && uuid.test(target || '') && file) {
    const evidence = await request(`/releases/${target}/evidence`);
    await writeFile(file, JSON.stringify(evidence, null, 2) + '\n', { flag: 'wx' });
    console.log('Evidence exported.');
  } else if (
    command === 'list' &&
    ['projects', 'runs', 'releases', 'scenarios', 'traces', 'incidents', 'policies'].includes(
      target,
    )
  ) {
    console.log(JSON.stringify(await request(`/${target}`), null, 2));
  } else
    throw new Error(
      'Usage: npm run cli -- gate RELEASE_ID | export RELEASE_ID FILE | list RESOURCE',
    );
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
