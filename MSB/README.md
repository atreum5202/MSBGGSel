# MyStealthBrowser (MSB)

Antidetect browser profile manager built on **Electron (Main Process)** + **Fastify (REST API)** + **React (Vite-based Renderer)**.

MSB isolates user sessions, configures unique fingerprints (UserAgent, Viewport, Timezone, Locale), routes network traffic via proxies, and injects customized extension badges.

> **Quick links**: see [`AGENT.md`](./AGENT.md) for the canonical stack + data model + API reference. See [`MORELOGIN_COMPAT_CHANGELOG.md`](./MORELOGIN_COMPAT_CHANGELOG.md) for the recent API/feature additions (MoreLogin parity, recycle bin, E2E encryption, etc.).

---

## 🏗 Architecture

MSB is a desktop app with three layers:

- **Renderer Process (React + Vite)** — dashboard at `http://127.0.0.1:17248/ui/`. Create, configure, and launch profiles.
- **Main Process (Electron)** — manages the Electron window, system tray, and starts browser subprocesses.
- **REST API & WebSockets (Fastify)** — local server on `http://127.0.0.1:17248`. CRUD on profiles, browser start/stop, real-time status & log streams.

```
┌─────────────────────────────────┐
│  Renderer (React + Vite)        │  http://127.0.0.1:17248/ui/
└────────────────┬────────────────┘
                 │ Bearer token from /ui-config
┌────────────────▼────────────────┐
│  Fastify (Main Process)         │  17248
│  • profiles / groups / trash    │
│  • browser start/stop/status    │
│  • recycle bin · rate limiter   │
│  • /api/env/* (ML-compatible)   │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│  BrowserLauncher (Main)         │  Patchright ⨯ CloakBrowser
│  • userDataDir per profile      │
│  • CDP endpoint for automation  │
└─────────────────────────────────┘
```

---

## 🚀 Engines: CloakBrowser + Patchright

| Engine | When to use | What it gives you |
|---|---|---|
| `cloakbrowser` (binary patches) | Production anti-detect; passed through Qrator/Cloudflare/Google | Source-level Chromium patches for Canvas, WebGL, AudioContext, Fonts |
| `patchright` (CDP patches) | When you need Playwright APIs (`recordVideo`, custom `IGNORE_DEFAULT_ARGS`, etc.) | Patches CDP without `--no-sandbox`, keeps `chromiumSandbox: true` |
| `auto` (default) | — | Cloak if the binary is installed, otherwise Patchright |

The launcher (`src/main/services/browserLauncher/index.js`) handles the full lifecycle: DPAPI Login Data injection, Google search defaults in `Preferences`, SOCKS5-auth via `undici` `ProxyAgent`, MSB Profile Badge extension, init-script anti-detection, pre-flight IP check, E2E key validation, CDP endpoint discovery for automation, and launch policies (`visible` / `minimized` / `background` / `headless`).

### Gemini / Google launch guidance

For Gemini or Google flows that should run "in the background", prefer `launchMode: "background"` over raw `headless: true`.

Why:
- `background` keeps a normal headed rendering pipeline, which is generally less suspicious for Google properties than true headless.
- `headless` remains available for compatibility and batch jobs, but it carries a higher detection risk.

Practical profile alignment for Google-facing sessions:
- keep `fingerprint.timezone`, `fingerprint.locale`, and proxy geo aligned
- keep viewport/screen realistic for the selected platform
- avoid mismatches between fonts/GPU/WebGL traits and the chosen OS/browser fingerprint
- enable `humanize` for profiles used with sign-in / prompt submission flows when possible

`background` is implemented as a best-effort mode: MSB suppresses focus as much as the engine/platform allows, starts the browser minimized, and attempts to keep the window off-screen. It is not a true hidden-window mode on Chromium, so some platforms may still briefly create a window.

### Browser CLI flags

Every Chromium session is launched with two MSB-specific flags so external tooling and the badge extension can identify it:

- `--msb-profile-number=N` — monotonic profile number
- `--msb-profile-email=email@example.com` — account email

The badge extension reads `msb-context.json` written by the launcher before each start.

---

## 🛠 Quick Start

```bash
npm install
npm run dev          # Vite + Electron with hot reload
# or
npm run build:renderer
npm start            # production launch
```

For more details and the full endpoint catalogue see [`AGENT.md`](./AGENT.md).
