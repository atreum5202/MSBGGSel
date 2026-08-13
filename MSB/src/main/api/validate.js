// JSON-Schema validation helpers built on Ajv.
//
// Use:
//   import { validateBody, validatePatch, REUSABLE } from './validate.js';
//   app.post('/profiles', {
//     schema: { body: { $ref: 'profile-create' } },
//     ...validateBody('profile-create'),
//   }, async (req) => { ... });
//
// Schemas are registered once with `register(name, schema)` and referenced
// by name in the route. PATCH endpoints use a special "patch" mode that
// forbids `id`, `schemaVersion`, `createdAt` and other server-managed fields.

import Ajv from 'ajv';

const ajv = new Ajv({
  allErrors: true,
  removeAdditional: false,    // keep unknown fields so we can warn
  useDefaults: false,
  coerceTypes: false,
  strict: false,
});
// Note: we intentionally do NOT call `addFormats` — ajv-formats 3.x has a
// different init API across versions and pulls in extra deps. The schemas
// below only use built-in types + regex patterns; if a route needs a format
// like "email" or "uri", use a regex pattern instead (e.g. for email).

const REGISTRY = new Map();

/**
 * Register a JSON schema under a name. The compiled validator is cached.
 */
export function register(name, schema) {
  if (REGISTRY.has(name)) return REGISTRY.get(name);
  const validate = ajv.compile(schema);
  REGISTRY.set(name, validate);
  return validate;
}

/**
 * Compile-on-demand (rare code path; for ad-hoc validation).
 */
export function compile(schema) {
  return ajv.compile(schema);
}

/**
 * Build a Fastify body validator from a registered schema name.
 *   { schema: { body: { $ref: 'profile-create' } } }   // not actually needed
 *
 *   app.post('/profiles', validateBody('profile-create'), handler)
 */
export function validateBody(name) {
  const validate = REGISTRY.get(name) || register(name, _pendingSchemas.get(name));
  if (!validate) throw new Error(`validateBody: schema "${name}" not registered`);
  return async (req, reply) => {
    if (!validate(req.body)) {
      reply.code(400);
      throw new Error(`Validation failed: ${ajv.errorsText(validate.errors)}`);
    }
  };
}

/**
 * PATCH-style validator: same as validateBody but rejects server-managed
 * fields (id, schemaVersion, createdAt, updatedAt, number).
 */
const PATCH_FORBIDDEN = new Set([
  'id', 'schemaVersion', 'createdAt', 'updatedAt', 'number',
  '_forwardCompat',
]);

export function validatePatch(name) {
  const validate = REGISTRY.get(name);
  if (!validate) throw new Error(`validatePatch: schema "${name}" not registered`);
  return async (req, reply) => {
    const body = req.body || {};
    for (const k of Object.keys(body)) {
      if (PATCH_FORBIDDEN.has(k)) {
        reply.code(400);
        throw new Error(`PATCH: field "${k}" is server-managed and cannot be updated`);
      }
    }
    if (!validate(body)) {
      reply.code(400);
      throw new Error(`Validation failed: ${ajv.errorsText(validate.errors)}`);
    }
  };
}

// Pending registry: register(name, schema) can be called before first
// use of validateBody(name). This is needed because module import order
// would otherwise force a chicken-and-egg.
const _pendingSchemas = new Map();
export function registerLazy(name, schema) {
  if (REGISTRY.has(name)) return REGISTRY.get(name);
  _pendingSchemas.set(name, schema);
}

// ─── Built-in schemas ────────────────────────────────────────────────────
// Profile create body — every field is optional. Engine defaults to 'auto'.
// Provider auto-detected from account.email if not supplied.
const PROFILE_CREATE = {
  $id: 'profile-create',
  type: 'object',
  additionalProperties: false,
  properties: {
    name: { type: 'string', minLength: 1, maxLength: 200 },
    engine: { type: 'string', enum: ['auto', 'patchright', 'cloakbrowser'] },
    group: { type: ['string', 'null'], maxLength: 100 },
    notes: { type: 'string', maxLength: 5000 },
    tags: { type: 'array', items: { type: 'string', minLength: 1, maxLength: 50 }, maxItems: 50, uniqueItems: true },
    proxyEnabled: { type: 'boolean' },
    proxy: {
      type: ['object', 'null'],
      properties: {
        protocol: { type: 'string', enum: ['http', 'https', 'socks4', 'socks5'] },
        host: { type: 'string', minLength: 1 },
        port: { type: 'integer', minimum: 1, maximum: 65535 },
        username: { type: 'string' },
        password: { type: 'string' },
      },
      required: ['protocol', 'host', 'port'],
      additionalProperties: false,
    },
    account: {
      type: 'object',
      additionalProperties: true,
      properties: {
        // email: simple regex instead of `format: 'email'` (no ajv-formats dep)
        email: { type: 'string', maxLength: 320, pattern: '^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$' },
        password: { type: 'string', maxLength: 1000 },
        name: { type: 'string' },
        loginStatus: { type: 'string', enum: ['ok', 'expired', 'error', 'unknown'] },
      },
    },
    fingerprint: {
      type: 'object',
      additionalProperties: true,
      properties: {
        userAgent: { type: 'string' },
        platform: { type: 'string' },
        locale: { type: 'string' },
        timezone: { type: 'string' },
        viewport: {
          type: 'object',
          properties: {
            width: { type: 'integer', minimum: 320, maximum: 7680 },
            height: { type: 'integer', minimum: 240, maximum: 4320 },
          },
          additionalProperties: false,
        },
        noise: { type: 'boolean' },
      },
    },
    extensions: { type: 'array', items: { type: 'string' } },
    aggressiveFingerprint: { type: 'boolean' },
    humanize: { type: 'boolean' },
    startUrl: { type: 'string', maxLength: 2000 },
    flagged: { type: 'boolean' },
    sortOrder: { type: 'integer' },
  },
};
register('profile-create', PROFILE_CREATE);

// PATCH body — same shape, every field optional, server-managed fields rejected.
register('profile-patch', PROFILE_CREATE);

// Browser start body — used by POST /profiles/:id/start and POST /api/env/start
const BROWSER_START = {
  $id: 'browser-start',
  type: 'object',
  additionalProperties: false,
  properties: {
    headless: { type: 'boolean' },
    isHeadless: { type: 'boolean' },
    launchMode: { type: 'string', enum: ['visible', 'minimized', 'background', 'headless'] },
    cdpEvasion: { type: 'boolean' },
    closeCheckIPpage: { type: 'boolean' },
    checkIPErrorHandle: { type: 'integer', enum: [1, 2] },
    encryptKey: { type: 'string', minLength: 1, maxLength: 1000 },
    extraArgs: { type: 'array', items: { type: 'string' } },
  },
};
register('browser-start', BROWSER_START);

// Recycle bin: trash a profile
const TRASH = {
  $id: 'trash-restore',
  type: 'object',
  additionalProperties: false,
  maxProperties: 0,
};
register('trash-restore', TRASH);
