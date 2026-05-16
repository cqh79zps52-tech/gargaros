#!/usr/bin/env node
// Tiny CLI wrapping `GargarosClient`. Used by the Claude Code subagent
// (~/.claude/agents/desktop.md) so it can `npx gargaros click 100 200`.

import { writeFileSync } from "node:fs";
import { GargarosClient, MouseButton } from "./index.js";

function usage(): never {
  // eslint-disable-next-line no-console
  console.error(
    "usage: gargaros <cmd> [args]\n  ping\n  click <x> <y> [left|right|middle] [--double]\n  move <x> <y>\n  type <text> [--enter]\n  key <vk> [mods]\n  scroll <dx> <dy>\n  screenshot <out.webp>\n  ui",
  );
  process.exit(2);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) usage();
  const client = new GargarosClient({ autoStream: false });
  try {
    await client.connect();
    const cmd = args[0];
    switch (cmd) {
      case "ping": {
        await client.ping();
        process.stdout.write("pong\n");
        break;
      }
      case "click": {
        const x = Number(args[1]);
        const y = Number(args[2]);
        const btnName = args[3] ?? "left";
        const btn =
          btnName === "right" ? MouseButton.Right : btnName === "middle" ? MouseButton.Middle : MouseButton.Left;
        const double = args.includes("--double");
        client.click(x, y, btn, double);
        break;
      }
      case "move": {
        client.move(Number(args[1]), Number(args[2]));
        break;
      }
      case "type": {
        client.type(args[1] ?? "", args.includes("--enter"));
        break;
      }
      case "key": {
        client.key(Number(args[1]), Number(args[2] ?? 0));
        break;
      }
      case "scroll": {
        client.scroll(Number(args[1]), Number(args[2]));
        break;
      }
      case "screenshot": {
        const out = args[1] ?? "screenshot.webp";
        const frame = await client.screenshot();
        writeFileSync(out, frame.data);
        process.stdout.write(`${frame.width}x${frame.height} written to ${out}\n`);
        break;
      }
      case "ui": {
        const snap = await client.uiSnapshot();
        process.stdout.write(
          `focus hwnd=${snap.focusedHwnd} title=${JSON.stringify(snap.focusedTitle)} dialog=${snap.activeDialog ?? "<none>"}\n`,
        );
        break;
      }
      default:
        usage();
    }
    // give fire-and-forget writes a tick to flush before closing
    await new Promise((r) => setImmediate(r));
  } finally {
    client.close();
  }
}

main().catch((e) => {
  // eslint-disable-next-line no-console
  console.error(`error: ${e instanceof Error ? e.message : String(e)}`);
  process.exit(1);
});
