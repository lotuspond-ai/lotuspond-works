// What the project's registry already holds for the open draft, judged from the versions the
// Studio has listed. The registry still decides admission when publishing; this only says
// early what it will answer, and suggests a version that is free.
import { VERSION } from './packageContract.js';

type Triple = [number, number, number];
// Each part of a v1 version is at most six digits.
const MAX_PART = 999999;

function parse(version: string): Triple | null {
  const match = VERSION.exec(version);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

// Highest version first, as the Studio lists them; anything unparsable sorts last.
export function compareVersions(a: string, b: string): number {
  const left = parse(a);
  const right = parse(b);
  if (!left || !right) return left ? -1 : right ? 1 : 0;
  for (let index = 0; index < 3; index += 1)
    if (left[index] !== right[index]) return right[index] - left[index];
  return 0;
}

// The next patch after the highest of the draft's version and every published one.
export function nextFreeVersion(published: readonly string[], current: string): string {
  const highest = [current, ...published].map(parse).filter((item): item is Triple => !!item);
  highest.sort((a, b) => compareVersions(b.join('.'), a.join('.')));
  const [major, minor, patch] = highest.at(-1) ?? [0, 0, 0];
  if (patch < MAX_PART) return `${major}.${minor}.${patch + 1}`;
  if (minor < MAX_PART) return `${major}.${minor + 1}.0`;
  return `${major + 1}.0.0`;
}

export type ListedVersion = { package_id: string; version: string; sha256: string };
export type Publication =
  | { state: 'unknown' }
  | { state: 'new' }
  | { state: 'same' }
  | { state: 'conflict'; suggestion: string };

export const sameContent = (version: ListedVersion, digest: string) =>
  !!digest && version.sha256 === digest;

// Without a digest (a draft that fails the check) nothing can be said about the content.
export function publication(
  listed: readonly ListedVersion[],
  pkg: { package_id: string; version: string },
  digest: string,
): Publication {
  if (!digest) return { state: 'unknown' };
  const ofPackage = listed.filter((item) => item.package_id === pkg.package_id);
  const taken = ofPackage.filter((item) => item.version === pkg.version);
  if (!taken.length) return { state: 'new' };
  if (taken.some((item) => sameContent(item, digest))) return { state: 'same' };
  return {
    state: 'conflict',
    suggestion: nextFreeVersion(
      ofPackage.map((item) => item.version),
      pkg.version,
    ),
  };
}
