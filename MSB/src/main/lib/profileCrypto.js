// Profile-level encryption helper (MoreLogin `encryptKey` parity).
//
// MoreLogin's `encryptKey` enables end-to-end encryption of the on-disk
// profile data (Cookies, LocalStorage, IndexedDB, extensions, etc.). In their
// product the encryption is anchored in the browser kernel layer, so the JS
// surface is just a key gate.
//
// MSB doesn't have a custom Chromium build, so we can't honestly replicate
// the kernel-level E2E. What we *can* do (and what this module provides):
//
//   1. A stable, audited AEAD primitive (AES-256-GCM) for the JS-visible
//      sensitive fields (account.password, account.notes, account.tokens,
//      any future secrets).
//   2. A KDF (scrypt) so user-supplied passphrases work without forcing a
//      32-byte hex string.
//   3. A `materialise(encryptKey)` helper that:
//        - Accepts a hex, base64, or passphrase string
//        - Returns either a 32-byte raw key (when a marker exists on disk)
//          or `null` (no encryption configured for this profile)
//   4. `envelopeFor(key, plaintext)` → base64 string safe to put in JSON
//   5. `openEnvelope(key, envelope)` → plaintext
//   6. `writeMarker(profileDir, key)` / `readMarker(profileDir)` — file
//      under <profileDir>/.msb-crypto.json that proves the on-disk sensitive
//      fields are encrypted with this key.
//
// We DO NOT pretend to encrypt the entire userDataDir. We document the scope
// clearly in MORELOGIN_COMPAT_CHANGELOG.md so users know what they get.

import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

const MARKER_FILE = '.msb-crypto.json';
const MARKER_VERSION = 1;
const SCRYPT_N = 16384;       // OWASP minimum for interactive logins
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const SCRYPT_KEYLEN = 32;
const SALT_BYTES = 16;
const IV_BYTES = 12;
const TAG_BYTES = 16;

/**
 * Best-effort parse of an `encryptKey` payload from the MoreLogin API surface.
 * Accepted formats:
 *   - 64-char hex string                (32 raw bytes, used directly)
 *   - 44-char base64 / urlsafe-base64   (32 raw bytes after decode)
 *   - 32-char ASCII passphrase          (passed through scrypt with a fixed
 *                                        profile-scoped salt)
 *
 * Returns { kind, key, salt? } or null when the input is unparseable.
 */
export function materialise(encryptKey, { profileId = '' } = {}) {
  if (typeof encryptKey !== 'string' || encryptKey.length === 0) return null;
  const trimmed = encryptKey.trim();

  // (1) 64-char hex → 32 raw bytes
  if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
    return { kind: 'hex', key: Buffer.from(trimmed, 'hex'), salt: null };
  }

  // (2) base64 / urlsafe-base64
  if (/^[A-Za-z0-9+/=_-]+$/.test(trimmed) && trimmed.length >= 40) {
    try {
      const norm = trimmed.replace(/-/g, '+').replace(/_/g, '/');
      const padded = norm + '='.repeat((4 - (norm.length % 4)) % 4);
      const buf = Buffer.from(padded, 'base64');
      if (buf.length === 32) return { kind: 'base64', key: buf, salt: null };
    } catch {
      // fall through
    }
  }

  // (3) passphrase → scrypt with a profile-scoped salt
  // Salt is deterministic per profile so the same key unlocks the same profile
  // on the same machine. Not perfect, but the documented scope is "make it
  // hard to read JSON with `cat`" rather than full disk encryption.
  const salt = crypto.createHash('sha256').update(`msb:profile-crypto:v${MARKER_VERSION}:${profileId}`).digest().subarray(0, SALT_BYTES);
  const key = crypto.scryptSync(trimmed, salt, SCRYPT_KEYLEN, { N: SCRYPT_N, r: SCRYPT_R, p: SCRYPT_P, maxmem: 64 * 1024 * 1024 });
  return { kind: 'passphrase', key, salt };
}

/**
 * AES-256-GCM encrypt a UTF-8 string. Output is a base64 string of
 * `iv || tag || ciphertext`. Caller stores the result.
 */
export function envelopeFor(key, plaintext) {
  if (!(key instanceof Buffer) || key.length !== 32) {
    throw new Error('envelopeFor: key must be a 32-byte Buffer');
  }
  const iv = crypto.randomBytes(IV_BYTES);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ct = Buffer.concat([cipher.update(String(plaintext ?? ''), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ct]).toString('base64');
}

/**
 * AES-256-GCM decrypt a base64 envelope. Throws on auth-tag mismatch.
 */
export function openEnvelope(key, envelope) {
  if (!(key instanceof Buffer) || key.length !== 32) {
    throw new Error('openEnvelope: key must be a 32-byte Buffer');
  }
  if (typeof envelope !== 'string' || envelope.length === 0) {
    return '';
  }
  const buf = Buffer.from(envelope, 'base64');
  if (buf.length < IV_BYTES + TAG_BYTES) {
    throw new Error('openEnvelope: envelope too short');
  }
  const iv = buf.subarray(0, IV_BYTES);
  const tag = buf.subarray(IV_BYTES, IV_BYTES + TAG_BYTES);
  const ct = buf.subarray(IV_BYTES + TAG_BYTES);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]).toString('utf8');
}

/**
 * Compute a non-reversible key fingerprint. Stored in the marker file so
 * we can validate "is this the right key for this profile?" without keeping
 * the key itself on disk.
 */
export function fingerprint(key) {
  if (!(key instanceof Buffer) || key.length !== 32) {
    throw new Error('fingerprint: key must be a 32-byte Buffer');
  }
  return crypto.createHash('sha256').update(key).digest('hex');
}

function markerPath(profileDir) {
  return path.join(profileDir, MARKER_FILE);
}

export async function readMarker(profileDir) {
  try {
    const raw = await fs.readFile(markerPath(profileDir), 'utf8');
    const parsed = JSON.parse(raw);
    if (parsed && parsed.version === MARKER_VERSION && parsed.fingerprint) {
      return parsed;
    }
  } catch { /* missing or corrupt */ }
  return null;
}

export async function writeMarker(profileDir, { fingerprint: fp, kind }) {
  const body = JSON.stringify({ version: MARKER_VERSION, fingerprint: fp, kind, createdAt: Date.now() }, null, 2);
  await fs.writeFile(markerPath(profileDir), body, { encoding: 'utf8', mode: 0o600 });
}

export async function deleteMarker(profileDir) {
  try { await fs.unlink(markerPath(profileDir)); } catch { /* ignore */ }
}

/**
 * Encrypt a record's sensitive fields. Mutates and returns the same object.
 * Fields that are encrypted:
 *   account.password
 *   account.notes
 *   account.tokens  (object: each value encrypted)
 */
export function encryptSensitiveFields(profile, key) {
  if (!key || !profile?.account) return profile;
  const acc = profile.account;
  if (typeof acc.password === 'string' && acc.password.length) {
    acc.passwordEnc = envelopeFor(key, acc.password);
    delete acc.password;
  }
  if (typeof acc.notes === 'string' && acc.notes.length) {
    acc.notesEnc = envelopeFor(key, acc.notes);
    delete acc.notes;
  }
  if (acc.tokens && typeof acc.tokens === 'object') {
    const enc = {};
    for (const [k, v] of Object.entries(acc.tokens)) {
      enc[k] = envelopeFor(key, v);
    }
    acc.tokensEnc = enc;
    delete acc.tokens;
  }
  return profile;
}

/**
 * Inverse of `encryptSensitiveFields`. Returns a new object.
 * When `key` is null but encrypted fields are present, leaves them as
 * `<encrypted:...>` placeholders rather than throwing.
 */
export function decryptSensitiveFields(profile, key) {
  if (!profile?.account) return profile;
  const acc = profile.account;
  if (acc.passwordEnc) {
    acc.password = key ? openEnvelope(key, acc.passwordEnc) : `<encrypted:${acc.passwordEnc.slice(0, 16)}…>`;
    delete acc.passwordEnc;
  }
  if (acc.notesEnc) {
    acc.notes = key ? openEnvelope(key, acc.notesEnc) : `<encrypted:${acc.notesEnc.slice(0, 16)}…>`;
    delete acc.notesEnc;
  }
  if (acc.tokensEnc && typeof acc.tokensEnc === 'object') {
    const tokens = {};
    for (const [k, v] of Object.entries(acc.tokensEnc)) {
      tokens[k] = key ? openEnvelope(key, v) : `<encrypted:${String(v).slice(0, 16)}…>`;
    }
    acc.tokens = tokens;
    delete acc.tokensEnc;
  }
  return profile;
}
