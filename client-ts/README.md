# @gargaros/client

Node.js client for the Gargaros Windows desktop daemon (`gargaros-server`).

Speaks the binary wire protocol defined in `rust/protocol` over the Named Pipe
`\\.\pipe\gargaros`. Designed to be embedded in Claude Code via a
`UserPromptSubmit` hook (so the model gets the live screen each turn without
ever calling a `screenshot` tool) and/or a dedicated `desktop` subagent.

## Install (local dev)

```powershell
cd client-ts
npm install
npm run build
```

Make sure `gargaros-server.exe` (from `rust/target/release/`) is running.

## Programmatic use

```ts
import { GargarosClient } from "@gargaros/client";

const g = new GargarosClient();
await g.connect();         // auto-subscribes to a 10 fps WebP stream
g.click(800, 400);         // fire-and-forget
g.type("hello world", true);

const snap = g.getCurrentState();  // <1 ms, no I/O
console.log(`age ${snap.ageMs}ms, focus=${snap.focus.title}`);
```

## Claude Code integration

### Option 1 — `UserPromptSubmit` hook

Copy `src/hook.ts` (or the built `dist/hook.js`) to
`~/.claude/hooks/desktop_state.ts` and reference it in your Claude Code
settings. Every prompt receives the latest frame + recent events as a
system message — Claude never asks for a screenshot.

### Option 3 — Dedicated subagent

Copy `agents/desktop.md` to `~/.claude/agents/desktop.md`. The main agent
delegates GUI work ("open Chrome and go to example.com") to the subagent,
which uses `npx gargaros …` commands.

### Option 4 — Native tools (fork)

For the lowest-latency integration, fork Claude Code, add `@gargaros/client`
as a dependency, and expose `desktop_click` / `desktop_type` / … as native
tools next to `Bash` and `Read`. See plan section 8.3.
