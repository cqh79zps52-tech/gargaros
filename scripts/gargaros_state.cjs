#!/usr/bin/env node
// Claude Code UserPromptSubmit hook for Gargaros.
//
// Reads the Claude Code hook JSON on stdin (ignored), opens the
// `\\.\pipe\gargaros` Named Pipe, asks for `Op::UiSnapshot` (0x30) and
// prints a one-line "DESKTOP_STATE: ..." summary on stdout. Claude Code
// appends the stdout text to the model's context.
//
// If the daemon is not running, the hook stays silent (exit 0) so prompts
// keep working without it.

const net = require("node:net");

const HEADER_SIZE = 8;
const PIPE = "\\\\.\\pipe\\gargaros";
const TIMEOUT_MS = 250; // strict — never block the prompt
const OP_UI_SNAPSHOT = 0x30;
const OP_UI_TREE = 0xa0;
const OP_ERR = 0xff;
const FLAG_ACK_REQUIRED = 0x01;

function encodeHeader(op, flags, seq, payloadLen) {
  const buf = Buffer.alloc(HEADER_SIZE);
  buf.writeUInt8(op, 0);
  buf.writeUInt8(flags, 1);
  buf.writeUInt16LE(seq, 2);
  buf.writeUInt32LE(payloadLen, 4);
  return buf;
}

function decodeUiTree(body) {
  if (body.length < 4) return { focusedTitle: "", activeDialog: null, focusedHwnd: 0n };
  const nodeCount = body.readUInt32LE(0);
  if (nodeCount < 1) return { focusedTitle: "", activeDialog: null, focusedHwnd: 0n };
  let off = 4;
  const hwnd = body.readBigInt64LE(off);
  off += 8;
  const tlen = body.readUInt16LE(off);
  off += 2;
  const title = body.subarray(off, off + tlen).toString("utf8");
  off += tlen;
  const dlen = body.readUInt16LE(off);
  off += 2;
  const dialog = body.subarray(off, off + dlen).toString("utf8");
  return { focusedHwnd: hwnd, focusedTitle: title, activeDialog: dialog || null };
}

function escape(s) {
  return s.replace(/\s+/g, " ").trim();
}

async function fetchSnapshot() {
  return await new Promise((resolve) => {
    const sock = net.createConnection(PIPE);
    let buf = Buffer.alloc(0);
    let done = false;

    const finish = (result) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch {}
      resolve(result);
    };

    const timer = setTimeout(() => finish(null), TIMEOUT_MS);
    sock.once("error", () => finish(null));
    sock.once("close", () => { clearTimeout(timer); });
    sock.on("connect", () => {
      const payload = Buffer.from([0]); // UiSnapshotPayload { flags: 0 }
      const hdr = encodeHeader(OP_UI_SNAPSHOT, FLAG_ACK_REQUIRED, 1, payload.length);
      sock.write(Buffer.concat([hdr, payload]));
    });
    sock.on("data", (chunk) => {
      buf = buf.length === 0 ? chunk : Buffer.concat([buf, chunk]);
      if (buf.length < HEADER_SIZE) return;
      const op = buf.readUInt8(0);
      const plen = buf.readUInt32LE(4);
      if (buf.length < HEADER_SIZE + plen) return;
      const body = buf.subarray(HEADER_SIZE, HEADER_SIZE + plen);
      clearTimeout(timer);
      if (op === OP_UI_TREE) finish(decodeUiTree(body));
      else if (op === OP_ERR) finish(null);
      else finish(null);
    });
  });
}

(async () => {
  // Drain stdin so Claude Code doesn't see a broken-pipe error.
  process.stdin.resume();
  process.stdin.on("data", () => {});
  process.stdin.on("end", () => {});

  const snap = await fetchSnapshot();
  if (!snap) {
    // Daemon offline — stay silent. Exit 0 so the prompt still runs.
    process.exit(0);
  }
  const parts = [`DESKTOP_STATE: focus=${JSON.stringify(escape(snap.focusedTitle))}`];
  if (snap.activeDialog) parts.push(`dialog=${JSON.stringify(escape(snap.activeDialog))}`);
  process.stdout.write(parts.join(" ") + "\n");
  process.exit(0);
})().catch(() => process.exit(0));
