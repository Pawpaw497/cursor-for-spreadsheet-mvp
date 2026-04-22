---
name: run-client
description: Starts and verifies the Vite React frontend (port 5173) for spreadsheet-cursor-mvp. Use when the user asks to run the client, start the dev server, open the web UI, or test the frontend; also when the agent needs a running browser for API-backed flows.
---

# Run client (Vite + React)

## Scope

- **Client root**: `client/` (Vite, React 18, AG Grid).
- **Default dev URL**: `http://localhost:5173` (see `client/vite.config.ts`).

## Start dev server

1. `cd` to the **repository root** or directly to `client/`.
2. If `client/node_modules` is missing, install: `cd client && npm install`.
3. Start: `cd client && npm run dev` (or `npm run dev` from `client/`).
4. The dev server is long-running. Run it in the **background** so the session stays usable; poll terminal output or open the URL to confirm it is up.

## Other scripts

| Command | Use |
|--------|-----|
| `npm run build` | Production build to `client/dist` |
| `npm run preview` | Serves the built app (Vite default port, often 4173) |

## Verification

- Expect a line similar to `Local: http://localhost:5173/` in the dev server log.
- If the port is in use, Vite may pick another port; read the log for the actual URL.

## With backend

- The API lives under `server/` (FastAPI). If the user needs end-to-end behavior, ensure the server is running per project docs or `README.md` in parallel with the client.

## Agent notes

- Use the project terminals folder to see whether `npm run dev` is already running before starting a duplicate.
- Do not block the user shell indefinitely on the dev command unless they explicitly want a foreground process.
