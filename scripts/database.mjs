import { mkdirSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const base = resolve('.local');
const data = resolve(base, 'postgres');
const socket = resolve(base, 'socket');
function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit' });
  if (result.error) throw new Error(`${command} is required. Install PostgreSQL 14 or later.`);
  if (result.status !== 0) process.exit(result.status || 1);
}
if (process.argv[2] === 'stop') {
  if (existsSync(resolve(data, 'postmaster.pid')))
    run('pg_ctl', ['-D', data, '-m', 'fast', 'stop']);
} else if (process.argv[2] === 'start') {
  mkdirSync(socket, { recursive: true, mode: 0o700 });
  if (!existsSync(resolve(data, 'PG_VERSION')))
    run('initdb', [
      '-D',
      data,
      '-U',
      'lotuspond',
      '--auth-local=trust',
      '--auth-host=trust',
      '--encoding=UTF8',
    ]);
  if (!existsSync(resolve(data, 'postmaster.pid')))
    run('pg_ctl', [
      '-D',
      data,
      '-l',
      resolve(base, 'postgres.log'),
      '-o',
      `-h 127.0.0.1 -p 55439 -k ${socket}`,
      'start',
    ]);
  const present = spawnSync(
    'psql',
    [
      '-h',
      '127.0.0.1',
      '-p',
      '55439',
      '-U',
      'lotuspond',
      '-d',
      'postgres',
      '-tAc',
      "SELECT 1 FROM pg_database WHERE datname='lotuspond_platform_dev'",
    ],
    { encoding: 'utf8' },
  );
  if (present.status !== 0) process.exit(1);
  if (present.stdout.trim() !== '1')
    run('createdb', ['-h', '127.0.0.1', '-p', '55439', '-U', 'lotuspond', 'lotuspond_platform_dev']);
  console.log('Local database is available on 127.0.0.1:55439. Use npm run db:init once.');
} else throw new Error('Use start or stop. This script does not reset or delete data.');
