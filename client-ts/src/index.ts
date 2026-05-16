// Gargaros Node.js client — connects to the Windows Named Pipe
// `\\.\pipe\gargaros` and exposes a small async API matching the binary
// protocol from `gargaros-protocol`.
//
// Design notes:
//   - The streaming subscription keeps the last-known frame and event list
//     up to date so `getCurrentState()` is a pointer read (used by the
//     Claude Code UserPromptSubmit hook).
//   - Action methods default to fire-and-forget per plan section 5.1.

import { Socket, createConnection } from "node:net";
import { EventEmitter } from "node:events";

import {
  Flags,
  FlagsValue,
  HEADER_SIZE,
  Header,
  MouseButton,
  MouseButtonValue,
  Op,
  OpValue,
  UiEvent,
  UiTreeSnapshot,
  decodeEventBody,
  decodeFrameBody,
  decodeHeader,
  decodeUiTree,
  encodeClick,
  encodeDrag,
  encodeHeader,
  encodeKey,
  encodeMove,
  encodeScreenshot,
  encodeScroll,
  encodeStreamSub,
  encodeType,
} from "./protocol.js";

export const PIPE_PATH = "\\\\.\\pipe\\gargaros";

export interface DesktopSnapshot {
  /** Latest captured frame, encoded as WebP. `null` until the first frame arrives. */
  frame: Buffer | null;
  /** Frame dimensions (after resize). */
  width: number;
  height: number;
  /** Age in ms of the latest frame. */
  ageMs: number;
  /** Currently focused window. */
  focus: { hwnd: bigint; title: string };
  /** Title of the active modal/dialog, or `null`. */
  dialog: string | null;
}

export interface ConnectOptions {
  pipePath?: string;
  /** Subscribe to a 10 fps stream on connect (default: true). */
  autoStream?: boolean;
  /** Frames-per-second for the auto stream (default: 10). */
  streamFps?: number;
}

export class GargarosClient extends EventEmitter {
  private socket: Socket | null = null;
  private rxBuf: Buffer = Buffer.alloc(0);
  private seq = 0;
  private pending = new Map<number, (body: Buffer, op: number) => void>();

  private lastFrame: Buffer | null = null;
  private lastFrameWidth = 0;
  private lastFrameHeight = 0;
  private lastFrameAt = 0;
  private focus: { hwnd: bigint; title: string } = { hwnd: 0n, title: "" };
  private dialog: string | null = null;
  private events: UiEvent[] = [];

  constructor(private readonly opts: ConnectOptions = {}) {
    super();
  }

  async connect(): Promise<void> {
    const path = this.opts.pipePath ?? PIPE_PATH;
    await new Promise<void>((resolve, reject) => {
      const sock = createConnection(path);
      sock.once("error", reject);
      sock.once("connect", () => {
        sock.removeListener("error", reject);
        this.socket = sock;
        sock.on("data", (buf) => this.onData(buf));
        sock.on("error", (err) => this.emit("error", err));
        sock.on("close", () => this.emit("close"));
        resolve();
      });
    });
    if (this.opts.autoStream !== false) {
      this.streamSubscribe(this.opts.streamFps ?? 10, true);
    }
  }

  close(): void {
    this.socket?.destroy();
    this.socket = null;
  }

  // ---------- synchronous read of latest desktop state ----------

  getCurrentState(): DesktopSnapshot {
    return {
      frame: this.lastFrame,
      width: this.lastFrameWidth,
      height: this.lastFrameHeight,
      ageMs: this.lastFrameAt === 0 ? -1 : Date.now() - this.lastFrameAt,
      focus: { ...this.focus },
      dialog: this.dialog,
    };
  }

  drainEvents(): UiEvent[] {
    const out = this.events;
    this.events = [];
    return out;
  }

  // ---------- action methods (fire-and-forget by default) ----------

  click(x: number, y: number, button: MouseButtonValue = MouseButton.Left, double = false): void {
    this.sendFireForget(Op.Click, encodeClick(x, y, button, double));
  }

  move(x: number, y: number, relative = false): void {
    this.sendFireForget(Op.Move, encodeMove(x, y, relative));
  }

  type(text: string, pressEnter = false): void {
    this.sendFireForget(Op.Type, encodeType(text, pressEnter));
  }

  key(vk: number, mods = 0): void {
    this.sendFireForget(Op.Key, encodeKey(vk, mods));
  }

  scroll(dx: number, dy: number): void {
    this.sendFireForget(Op.Scroll, encodeScroll(dx, dy));
  }

  drag(
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    button: MouseButtonValue = MouseButton.Left,
  ): void {
    this.sendFireForget(Op.Drag, encodeDrag(x1, y1, x2, y2, button));
  }

  // ---------- request/response helpers ----------

  async ping(): Promise<void> {
    const { op } = await this.request(Op.Ping, Flags.AckRequired, Buffer.alloc(0));
    if (op !== Op.Pong) throw new Error(`unexpected response opcode 0x${op.toString(16)}`);
  }

  async screenshot(monitor = 0): Promise<{ width: number; height: number; format: number; data: Buffer }> {
    const { op, body } = await this.request(
      Op.ScreenshotNow,
      Flags.AckRequired,
      encodeScreenshot(monitor),
    );
    if (op !== Op.Frame) throw new Error(`unexpected response opcode 0x${op.toString(16)}`);
    return decodeFrameBody(body);
  }

  async uiSnapshot(): Promise<UiTreeSnapshot> {
    const { op, body } = await this.request(Op.UiSnapshot, Flags.AckRequired, Buffer.from([0]));
    if (op !== Op.UiTree) throw new Error(`unexpected response opcode 0x${op.toString(16)}`);
    return decodeUiTree(body);
  }

  streamSubscribe(fps: number, diffOnly = true): void {
    if (!this.socket) throw new Error("not connected");
    const payload = encodeStreamSub(fps, diffOnly);
    const hdr = encodeHeader(Op.StreamSub, Flags.None, this.nextSeq(), payload.length);
    this.socket.write(Buffer.concat([hdr, payload]));
  }

  // ---------- internals ----------

  private nextSeq(): number {
    this.seq = (this.seq + 1) & 0xffff;
    return this.seq || 1;
  }

  private sendFireForget(op: OpValue, payload: Buffer): void {
    if (!this.socket) throw new Error("not connected");
    const hdr = encodeHeader(op, Flags.FireForget, this.nextSeq(), payload.length);
    this.socket.write(Buffer.concat([hdr, payload]));
  }

  private request(op: OpValue, flags: FlagsValue, payload: Buffer): Promise<{ op: number; body: Buffer }> {
    return new Promise((resolve, reject) => {
      if (!this.socket) return reject(new Error("not connected"));
      const seq = this.nextSeq();
      const hdr = encodeHeader(op, flags, seq, payload.length);
      const timer = setTimeout(() => {
        this.pending.delete(seq);
        reject(new Error(`request 0x${op.toString(16)} timed out`));
      }, 5000);
      this.pending.set(seq, (body, respOp) => {
        clearTimeout(timer);
        resolve({ op: respOp, body });
      });
      this.socket.write(Buffer.concat([hdr, payload]));
    });
  }

  private onData(chunk: Buffer): void {
    this.rxBuf = this.rxBuf.length === 0 ? chunk : Buffer.concat([this.rxBuf, chunk]);
    while (this.rxBuf.length >= HEADER_SIZE) {
      const hdr: Header = decodeHeader(this.rxBuf);
      const total = HEADER_SIZE + hdr.payloadLen;
      if (this.rxBuf.length < total) return;
      const body = this.rxBuf.subarray(HEADER_SIZE, total);
      this.dispatch(hdr, body);
      this.rxBuf = this.rxBuf.subarray(total);
    }
  }

  private dispatch(hdr: Header, body: Buffer): void {
    // Server-pushed frames live outside the request/response correlation.
    if (hdr.op === Op.Frame) {
      try {
        const f = decodeFrameBody(body);
        this.lastFrame = Buffer.from(f.data);
        this.lastFrameWidth = f.width;
        this.lastFrameHeight = f.height;
        this.lastFrameAt = Date.now();
        this.emit("frame", { width: f.width, height: f.height, format: f.format });
      } catch (e) {
        this.emit("error", e);
      }
      // also resolve any pending screenshot request waiting on this seq
      const p = this.pending.get(hdr.seq);
      if (p) {
        this.pending.delete(hdr.seq);
        p(body, hdr.op);
      }
      return;
    }
    if (hdr.op === Op.Event) {
      try {
        const evt = decodeEventBody(body);
        this.events.push(evt);
        // Maintain the cached focus / dialog state from the event stream too.
        const { EventKind } = require("./protocol.js");
        if (evt.kind === EventKind.WindowFocus) {
          this.focus = { hwnd: evt.hwnd, title: evt.title };
        } else if (evt.kind === EventKind.DialogOpened) {
          this.dialog = evt.title;
        } else if (evt.kind === EventKind.DialogClosed) {
          this.dialog = null;
        }
        this.emit("ui-event", evt);
      } catch (e) {
        this.emit("error", e);
      }
      return;
    }
    const pending = this.pending.get(hdr.seq);
    if (pending) {
      this.pending.delete(hdr.seq);
      pending(body, hdr.op);
      return;
    }
    if (hdr.op === Op.Err) {
      this.emit(
        "error",
        new Error(`server error 0x${body[0]?.toString(16) ?? "??"}: ${body.subarray(3).toString("utf8")}`),
      );
    }
  }
}

export { Op, Flags, MouseButton } from "./protocol.js";
export type { UiEvent, UiTreeSnapshot } from "./protocol.js";
