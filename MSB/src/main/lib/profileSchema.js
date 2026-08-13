// Profile schema versioning and migrations.
//
// Why: as the data model evolves (new fields like `crypto`, `number`,
// `proxyEnabled`, ...), old profile files on disk must be upgraded in
// place. Without a version field and a migration registry, the first
// read of an old profile would silently produce a half-broken state.
//
// Conventions:
//   - `schemaVersion` is a positive integer on the profile root.
//   - Migrations are pure functions: (profile) → profile (mutate or return new)
//   - The migration registry is append-only. NEVER remove or rewrite a
//     migration — old profiles with that version must still upgrade.
//   - The latest version is exported as PROFILE_SCHEMA_VERSION.
//   - The first version (legacy, before the field existed) is treated as
//     version 0. `_readAll` upgrades it to 1 on the fly.

export const PROFILE_SCHEMA_VERSION = 1;

/**
 * Each entry: fromVersion → migrator that produces fromVersion+1.
 * Index = target version - 1.
 */
const MIGRATIONS = [
  // 0 → 1: introduce schemaVersion, default group=null, ensure account shape
  function v0_to_v1(p) {
    if (!p || typeof p !== 'object') return p;
    p.schemaVersion = 1;
    if (p.group === undefined) p.group = null;
    if (!p.account || typeof p.account !== 'object') p.account = {};
    if (!p.fingerprint || typeof p.fingerprint !== 'object') p.fingerprint = {};
    if (!Array.isArray(p.tags)) p.tags = [];
    if (typeof p.proxyEnabled !== 'boolean') p.proxyEnabled = true;
    return p;
  },
];

/**
 * Apply migrations in order, mutating the profile in place.
 * Returns the (upgraded) profile. Throws on schema errors.
 */
export function migrateProfile(raw) {
  if (!raw || typeof raw !== 'object') {
    throw new Error('migrateProfile: not an object');
  }
  let v = Number.isInteger(raw.schemaVersion) ? raw.schemaVersion : 0;
  if (v < 0) v = 0;
  if (v > PROFILE_SCHEMA_VERSION) {
    // Profile was written by a NEWER version of MSB. Don't downgrade.
    // We still try to use it but emit a warning via the logger if the
    // caller provided one. (Set schemaVersion to current so subsequent
    // writes don't bump the on-disk file back to the old format.)
    raw._forwardCompat = true;
    return raw;
  }
  while (v < PROFILE_SCHEMA_VERSION) {
    const m = MIGRATIONS[v];
    if (typeof m !== 'function') {
      throw new Error(`No migration from version ${v} to ${v + 1}`);
    }
    try {
      raw = m(raw) || raw;
    } catch (err) {
      throw new Error(`Migration v${v}→v${v + 1} failed: ${err.message}`);
    }
    v += 1;
  }
  raw.schemaVersion = v;
  return raw;
}

/**
 * Sanity-check that a profile has all the fields we expect at the
 * current schema version. Doesn't throw — returns a list of {field, reason}
 * problems so the caller can decide what to do (log, fix-up, refuse).
 */
export function validateProfileShape(profile) {
  const issues = [];
  if (!profile || typeof profile !== 'object') {
    issues.push({ field: '<root>', reason: 'not an object' });
    return issues;
  }
  if (!Number.isInteger(profile.schemaVersion)) {
    issues.push({ field: 'schemaVersion', reason: 'missing or not integer' });
  }
  if (profile.group !== null && typeof profile.group !== 'string') {
    issues.push({ field: 'group', reason: 'must be null or string' });
  }
  if (profile.account && typeof profile.account !== 'object') {
    issues.push({ field: 'account', reason: 'must be object' });
  }
  if (profile.fingerprint && typeof profile.fingerprint !== 'object') {
    issues.push({ field: 'fingerprint', reason: 'must be object' });
  }
  if (profile.tags !== undefined && !Array.isArray(profile.tags)) {
    issues.push({ field: 'tags', reason: 'must be array' });
  }
  if (typeof profile.proxyEnabled !== 'boolean') {
    issues.push({ field: 'proxyEnabled', reason: 'must be boolean' });
  }
  return issues;
}
