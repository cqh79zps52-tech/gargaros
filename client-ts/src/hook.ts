// Claude Code `UserPromptSubmit` hook — injects live desktop state into
// every turn so the model has frame + events without ever calling
// screenshot() explicitly.
//
// Install at: ~/.claude/hooks/desktop_state.ts (TypeScript hook) or
// ~/.claude/hooks/desktop_state.cjs (after running `npm run build`).
//
// See plan section 5.2 / 8.1.

import { GargarosClient } from "./index.js";

interface HookContext {
  prompt?: string;
  attachments?: unknown[];
}

interface HookResult {
  prependSystemMessage?: string;
  attachImage?: Buffer;
}

let clientPromise: Promise<GargarosClient> | null = null;

function getClient(): Promise<GargarosClient> {
  if (!clientPromise) {
    const client = new GargarosClient({ autoStream: true, streamFps: 10 });
    clientPromise = client
      .connect()
      .then(() => client)
      .catch((err) => {
        clientPromise = null;
        throw err;
      });
  }
  return clientPromise;
}

export default async function hook(_ctx: HookContext): Promise<HookResult> {
  let client: GargarosClient;
  try {
    client = await getClient();
  } catch (e) {
    // Don't break Claude if the daemon isn't running.
    return {
      prependSystemMessage: `DESKTOP_STATE unavailable: ${(e as Error).message}. The gargaros-server daemon is not running.`,
    };
  }
  const state = client.getCurrentState();
  const events = client.drainEvents();
  const lines: string[] = [];
  lines.push(`DESKTOP_STATE (age ${state.ageMs}ms)`);
  lines.push(`active_window=${JSON.stringify(state.focus.title)}`);
  if (state.dialog) lines.push(`dialog_open=${JSON.stringify(state.dialog)}`);
  if (events.length > 0) {
    const summary = events
      .slice(-8)
      .map((e) => `kind=${e.kind} title=${JSON.stringify(e.title)}`)
      .join("; ");
    lines.push(`recent_events: ${summary}`);
  }
  const result: HookResult = { prependSystemMessage: lines.join("\n") };
  if (state.frame) result.attachImage = state.frame;
  return result;
}
