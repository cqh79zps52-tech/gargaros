# Gargaros radical plan — implementation notes

This document tracks how the v0.6 Rust rewrite implements the *Plan technique
radical* (PDF in private docs). Each section maps to a phase of the plan.

## Layout

```
rust/
  protocol/      gargaros-protocol  — opcodes, headers, payload structs (no_std-friendly)
  core/          gargaros-core      — input thread, ring buffer, capture, UI hook, pipe server
                  bin/server.rs     — gargaros-server daemon
  napi/          gargaros-napi      — Node.js NAPI bindings (stub; placeholder for option 4)
  cli/           gargaros-cli       — gargaros test CLI
  Cargo.toml     workspace          — single `cargo build` builds everything
  rust-toolchain.toml                 pins stable-x86_64-pc-windows-gnu

client-ts/
  src/protocol.ts    Buffer-based mirror of the wire protocol
  src/index.ts       GargarosClient (async, EventEmitter)
  src/cli.ts         `gargaros` CLI used by the subagent
  src/hook.ts        Claude Code UserPromptSubmit hook
  agents/desktop.md  Drop into ~/.claude/agents/desktop.md
```

## Phase 1 — Cargo workspace

- Four crates (`protocol`, `core`, `napi`, `cli`) sharing pinned versions in
  the workspace `Cargo.toml`.
- Toolchain pinned to `stable-x86_64-pc-windows-gnu` (uses MinGW from
  `E:\ikeep\mingw64\bin`). Rustup itself lives on D:
  (`D:\rust\rustup`, `D:\rust\cargo`).
- `rustfmt.toml` + `clippy.toml` enforce style.

## Phase 2 — Win32 input thread

`rust/core/src/input.rs`:

- `InputThread::spawn()` returns an `InputHandle` (clone-safe) backed by a
  4096-slot `crossbeam_channel::bounded` queue.
- The dedicated OS thread is named `gargaros-input`. It calls `SendInput`
  off the Tokio scheduler (plan section 10, *Async + SendInput*).
- Text uses `KEYEVENTF_UNICODE` + UTF-16 surrogates, so layouts (AZERTY,
  JIS) are not a concern (plan section 10, *Layouts clavier*).
- Mouse coordinates are normalised to the virtual screen via
  `GetSystemMetrics(SM_CX|CYVIRTUALSCREEN)`.

## Phase 3 — Capture + ring buffer

`rust/core/src/ring.rs` + `rust/core/src/capture.rs`:

- `Ring` allocates either page-aligned anonymous virtual memory (default) or
  a named `CreateFileMappingW` mapping (for cross-process readers).
- Writer uses a seqlock (`seq` even = stable, odd = in-flight). Readers
  retry on torn reads. `xxh3` per-frame hash drives the streaming diff mode.
- Capture thread runs `BitBlt` of the virtual screen at 60 Hz on a thread
  named `gargaros-capture`. A future revision can swap in
  `Windows.Graphics.Capture` for protected windows (plan section 10).

## Phase 4 — Binary protocol + Named Pipe

`rust/protocol/src/lib.rs` defines the opcodes, headers and packed payload
structs (`#[repr(C, packed)]` + `zerocopy::AsBytes/FromBytes`). A `CLICK` on
the wire is 18 bytes (8-byte header + 10-byte payload), per plan section 3.

`rust/core/src/pipe.rs` runs a Tokio Named-Pipe server at
`\\.\pipe\gargaros` in `PIPE_TYPE_MESSAGE` mode so the kernel handles
framing. Each client gets one task; the per-task loop reads a header, then
the payload, then dispatches.

`rust/cli/src/main.rs` is the reference Rust client for E2E testing.

## Phase 5 — UI tree + WinEventHook

`rust/core/src/ui.rs`:

- `UiThread::spawn()` creates a dedicated OS thread that registers four
  `SetWinEventHook(WINEVENT_OUTOFCONTEXT)` hooks (foreground, dialog
  start/end, object create/destroy, object focus) and drives a Win32
  message loop so the hook callbacks fire.
- Callback writes the latest focus / dialog into a `parking_lot::Mutex<UiSnapshot>`
  and pushes a `UiEvent` to a `tokio::sync::broadcast` channel that the
  pipe layer can subscribe to.
- A full UIA tree cache is left for a follow-up; the current cache covers
  the streaming hook's needs (focus title + dialog title).

## Phase 6 — Streaming + Claude Code

`rust/core/src/stream.rs` resizes BGRA to ≤1568 px wide
(`fast_image_resize`), encodes as WebP Q70 (`webp` crate), and prefixes the
result with a `FrameHeader`. `STREAM_SUB { fps, mode }` opens a per-client
Tokio task that pushes `FRAME` (0x90) messages at the requested FPS, with
`xxh3` hash-gated dedup in diff mode.

`client-ts/` provides the npm package `@gargaros/client`:

- `GargarosClient` (EventEmitter) auto-subscribes to a 10 fps stream on
  connect and exposes `getCurrentState()` / `drainEvents()` for the hook.
- `src/hook.ts` is the `UserPromptSubmit` hook: drop it (or the built
  `dist/hook.js`) at `~/.claude/hooks/desktop_state.ts` and Claude never
  has to call `screenshot()` (plan section 5.2).
- `agents/desktop.md` is the dedicated subagent for plan section 8.2.

## What's not done yet

- `Op::UiClickLabel` and `Op::FindText` return `Unimplemented` — they need
  the UIA tree cache (started in `ui.rs`) plus an OCR backend.
- The NAPI crate (`rust/napi/`) is a stub. Wiring `napi-rs` to expose
  `desktop_click` etc. as Node-native functions is the plan-section-8.3
  "fork Claude Code" path; pulling that crate in is straightforward but
  changes the publish surface, so it waits until the protocol is frozen.
- The capture thread uses GDI `BitBlt`. WGC (`Windows.Graphics.Capture`)
  for protected windows is the documented fallback (plan section 10).
- Benchmarks (plan section 9): need a `criterion` harness in `core/` to
  measure click p50/p99, ring read p50/p99, encode p50/p99.

## Building & running

```powershell
# Rust workspace
$env:RUSTUP_HOME='D:\rust\rustup'; $env:CARGO_HOME='D:\rust\cargo'
$env:PATH="D:\rust\cargo\bin;E:\ikeep\mingw64\bin;$env:PATH"
cd D:\Work\Brain1\Gargaros\rust
cargo build --release            # produces gargaros-server.exe + gargaros.exe
cargo test --workspace --release

# Daemon
.\target\release\gargaros-server.exe
# In another shell:
.\target\release\gargaros.exe ping                  # -> "got opcode 0x81"
.\target\release\gargaros.exe screenshot out.bin    # streams FRAME 0x90

# Node client
cd ..\client-ts
npm install
npm run build
node dist/cli.js ping                # -> pong
node dist/cli.js screenshot out.webp # WebP, ≤1568 px wide
node dist/cli.js ui                  # active window + dialog
```

## Benchmarks vs. plan targets

E2E smoke (Chrome focused, 1 connected client):

| Operation                  | Plan target (p50/p99) | Observed (smoke) |
| -------------------------- | --------------------- | ---------------- |
| `ping` round-trip          | 0.8 ms / 3 ms         | well under 5 ms  |
| `screenshot` WebP body     | ~5 ms / 12 ms encode  | 73 KB @ 1568×N   |

Full p50/p99 numbers require the `criterion` harness mentioned above.
