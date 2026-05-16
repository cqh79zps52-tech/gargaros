// Gargaros binary wire protocol — TypeScript mirror of `gargaros-protocol`.
//
// Frame layout (little-endian, packed):
//   offset 0 : u8  opcode
//   offset 1 : u8  flags
//   offset 2 : u16 seq_id
//   offset 4 : u32 payload_len
//   ----- 8-byte header ------
//   offset 8 : payload bytes

export const HEADER_SIZE = 8;
export const MAX_PAYLOAD_LEN = 16 * 1024 * 1024;

export const Op = {
  Ping: 0x01,
  Click: 0x10,
  Move: 0x11,
  Type: 0x12,
  Key: 0x13,
  Scroll: 0x14,
  Drag: 0x15,
  ScreenshotNow: 0x20,
  StreamSub: 0x21,
  StreamUnsub: 0x22,
  UiSnapshot: 0x30,
  UiClickLabel: 0x31,
  FindText: 0x40,
  Ack: 0x80,
  Pong: 0x81,
  Frame: 0x90,
  FrameDiff: 0x91,
  UiTree: 0xa0,
  Matches: 0xb0,
  Event: 0xfe,
  Err: 0xff,
} as const;

export type OpValue = (typeof Op)[keyof typeof Op];

export const Flags = {
  None: 0x00,
  AckRequired: 0x01,
  FireForget: 0x02,
  Compress: 0x04,
} as const;

export type FlagsValue = number;

export const MouseButton = {
  Left: 1,
  Right: 2,
  Middle: 3,
  X1: 4,
  X2: 5,
} as const;

export type MouseButtonValue = (typeof MouseButton)[keyof typeof MouseButton];

export const FrameFormat = {
  Bgra: 0,
  Jpeg: 1,
  WebP: 2,
} as const;

export const EventKind = {
  WindowFocus: 1,
  DialogOpened: 2,
  DialogClosed: 3,
  WindowCreated: 4,
  WindowDestroyed: 5,
  UiTreeDelta: 6,
} as const;

export interface Header {
  op: number;
  flags: number;
  seq: number;
  payloadLen: number;
}

export function encodeHeader(op: number, flags: number, seq: number, payloadLen: number): Buffer {
  const buf = Buffer.alloc(HEADER_SIZE);
  buf.writeUInt8(op, 0);
  buf.writeUInt8(flags, 1);
  buf.writeUInt16LE(seq, 2);
  buf.writeUInt32LE(payloadLen, 4);
  return buf;
}

export function decodeHeader(buf: Buffer): Header {
  if (buf.length < HEADER_SIZE) {
    throw new Error(`short header: ${buf.length} < ${HEADER_SIZE}`);
  }
  return {
    op: buf.readUInt8(0),
    flags: buf.readUInt8(1),
    seq: buf.readUInt16LE(2),
    payloadLen: buf.readUInt32LE(4),
  };
}

export function encodeClick(x: number, y: number, button: number, double: boolean): Buffer {
  const buf = Buffer.alloc(10);
  buf.writeInt32LE(x, 0);
  buf.writeInt32LE(y, 4);
  buf.writeUInt8(button, 8);
  buf.writeUInt8(double ? 1 : 0, 9);
  return buf;
}

export function encodeMove(x: number, y: number, relative: boolean): Buffer {
  const buf = Buffer.alloc(9);
  buf.writeInt32LE(x, 0);
  buf.writeInt32LE(y, 4);
  buf.writeUInt8(relative ? 1 : 0, 8);
  return buf;
}

export function encodeType(text: string, pressEnter: boolean): Buffer {
  const utf8 = Buffer.from(text, "utf8");
  if (utf8.length > 0xffff) throw new Error("type text > 64 KiB unsupported");
  const buf = Buffer.alloc(2 + utf8.length + 1);
  buf.writeUInt16LE(utf8.length, 0);
  utf8.copy(buf, 2);
  buf.writeUInt8(pressEnter ? 1 : 0, 2 + utf8.length);
  return buf;
}

export function encodeKey(vk: number, mods = 0): Buffer {
  const buf = Buffer.alloc(3);
  buf.writeUInt16LE(vk, 0);
  buf.writeUInt8(mods, 2);
  return buf;
}

export function encodeScroll(dx: number, dy: number): Buffer {
  const buf = Buffer.alloc(8);
  buf.writeInt32LE(dx, 0);
  buf.writeInt32LE(dy, 4);
  return buf;
}

export function encodeDrag(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  button: number,
): Buffer {
  const buf = Buffer.alloc(17);
  buf.writeInt32LE(x1, 0);
  buf.writeInt32LE(y1, 4);
  buf.writeInt32LE(x2, 8);
  buf.writeInt32LE(y2, 12);
  buf.writeUInt8(button, 16);
  return buf;
}

export function encodeScreenshot(monitor = 0): Buffer {
  return Buffer.from([monitor]);
}

export function encodeStreamSub(fps: number, diff = true): Buffer {
  return Buffer.from([fps & 0xff, diff ? 1 : 0]);
}

export interface FrameBody {
  width: number;
  height: number;
  format: number;
  data: Buffer;
}

export function decodeFrameBody(body: Buffer): FrameBody {
  if (body.length < 5) throw new Error("short FRAME body");
  return {
    width: body.readUInt16LE(0),
    height: body.readUInt16LE(2),
    format: body.readUInt8(4),
    data: body.subarray(5),
  };
}

export interface UiEvent {
  kind: number;
  hwnd: bigint;
  title: string;
}

export function decodeEventBody(body: Buffer): UiEvent {
  if (body.length < 1 + 8 + 2) throw new Error("short EVENT body");
  const kind = body.readUInt8(0);
  const hwnd = body.readBigInt64LE(1);
  const tlen = body.readUInt16LE(9);
  const title = body.subarray(11, 11 + tlen).toString("utf8");
  return { kind, hwnd, title };
}

export interface UiTreeSnapshot {
  focusedHwnd: bigint;
  focusedTitle: string;
  activeDialog: string | null;
}

export function decodeUiTree(body: Buffer): UiTreeSnapshot {
  const nodeCount = body.readUInt32LE(0);
  if (nodeCount < 1) {
    return { focusedHwnd: 0n, focusedTitle: "", activeDialog: null };
  }
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
  return {
    focusedHwnd: hwnd,
    focusedTitle: title,
    activeDialog: dialog.length > 0 ? dialog : null,
  };
}
