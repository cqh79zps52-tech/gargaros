//! Gargaros binary wire protocol.
//!
//! Frame layout (little-endian, packed):
//!   offset 0 : u8  opcode
//!   offset 1 : u8  flags
//!   offset 2 : u16 seq_id           (correlation request/response)
//!   offset 4 : u32 payload_len      (max 16 MB)
//!   ----- 8-byte header ------------------------------------------
//!   offset 8 : payload bytes (opcode-specific, never JSON)

#![cfg_attr(not(feature = "std"), no_std)]
#![allow(clippy::cast_possible_truncation)]

use core::convert::TryFrom;

use bitflags::bitflags;
use zerocopy::{AsBytes, FromBytes, FromZeroes};

pub const MAX_PAYLOAD_LEN: u32 = 16 * 1024 * 1024;

#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Op {
    Ping = 0x01,

    Click = 0x10,
    Move = 0x11,
    Type = 0x12,
    Key = 0x13,
    Scroll = 0x14,
    Drag = 0x15,

    ScreenshotNow = 0x20,
    StreamSub = 0x21,
    StreamUnsub = 0x22,

    UiSnapshot = 0x30,
    UiClickLabel = 0x31,

    FindText = 0x40,

    Ack = 0x80,
    Pong = 0x81,
    Frame = 0x90,
    FrameDiff = 0x91,
    UiTree = 0xA0,
    Matches = 0xB0,
    Event = 0xFE,
    Err = 0xFF,
}

impl TryFrom<u8> for Op {
    type Error = ProtoError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        Ok(match value {
            0x01 => Op::Ping,
            0x10 => Op::Click,
            0x11 => Op::Move,
            0x12 => Op::Type,
            0x13 => Op::Key,
            0x14 => Op::Scroll,
            0x15 => Op::Drag,
            0x20 => Op::ScreenshotNow,
            0x21 => Op::StreamSub,
            0x22 => Op::StreamUnsub,
            0x30 => Op::UiSnapshot,
            0x31 => Op::UiClickLabel,
            0x40 => Op::FindText,
            0x80 => Op::Ack,
            0x81 => Op::Pong,
            0x90 => Op::Frame,
            0x91 => Op::FrameDiff,
            0xA0 => Op::UiTree,
            0xB0 => Op::Matches,
            0xFE => Op::Event,
            0xFF => Op::Err,
            other => return Err(ProtoError::UnknownOpcode(other)),
        })
    }
}

bitflags! {
    #[derive(Copy, Clone, Debug, PartialEq, Eq)]
    pub struct Flags: u8 {
        const ACK_REQUIRED = 0b0000_0001;
        const FIRE_FORGET  = 0b0000_0010;
        const COMPRESS     = 0b0000_0100;
    }
}

#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ErrCode {
    Generic = 1,
    BadHeader = 2,
    BadPayload = 3,
    Unimplemented = 4,
    QueueFull = 5,
    Internal = 6,
    PermissionDenied = 7,
    NotFound = 8,
}

#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum FrameFormat {
    Bgra = 0,
    Jpeg = 1,
    WebP = 2,
}

impl TryFrom<u8> for FrameFormat {
    type Error = ProtoError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        Ok(match value {
            0 => FrameFormat::Bgra,
            1 => FrameFormat::Jpeg,
            2 => FrameFormat::WebP,
            other => return Err(ProtoError::BadField("frame_format", other as u32)),
        })
    }
}

#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum MouseButton {
    Left = 1,
    Right = 2,
    Middle = 3,
    X1 = 4,
    X2 = 5,
}

impl TryFrom<u8> for MouseButton {
    type Error = ProtoError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        Ok(match value {
            1 => MouseButton::Left,
            2 => MouseButton::Right,
            3 => MouseButton::Middle,
            4 => MouseButton::X1,
            5 => MouseButton::X2,
            other => return Err(ProtoError::BadField("button", other as u32)),
        })
    }
}

#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum EventKind {
    WindowFocus = 1,
    DialogOpened = 2,
    DialogClosed = 3,
    WindowCreated = 4,
    WindowDestroyed = 5,
    UiTreeDelta = 6,
}

// =============================================================================
// Fixed payload structs (packed, zero-copy)
// =============================================================================

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct Header {
    pub op: u8,
    pub flags: u8,
    pub seq: u16,
    pub payload_len: u32,
}

impl Header {
    pub const SIZE: usize = 8;

    pub fn new(op: Op, flags: Flags, seq: u16, payload_len: u32) -> Self {
        Self { op: op as u8, flags: flags.bits(), seq, payload_len }
    }

    pub fn parse(buf: &[u8]) -> Result<Self, ProtoError> {
        if buf.len() < Self::SIZE {
            return Err(ProtoError::ShortBuffer { needed: Self::SIZE, got: buf.len() });
        }
        Self::read_from(&buf[..Self::SIZE]).ok_or(ProtoError::BadHeader)
    }

    pub fn write_into(&self, buf: &mut [u8]) -> Result<(), ProtoError> {
        if buf.len() < Self::SIZE {
            return Err(ProtoError::ShortBuffer { needed: Self::SIZE, got: buf.len() });
        }
        buf[..Self::SIZE].copy_from_slice(self.as_bytes());
        Ok(())
    }

    pub fn flags(&self) -> Flags {
        Flags::from_bits_truncate(self.flags)
    }
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct ClickPayload {
    pub x: i32,
    pub y: i32,
    pub button: u8,
    pub double: u8,
}

impl ClickPayload {
    pub const SIZE: usize = 10;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct MovePayload {
    pub x: i32,
    pub y: i32,
    pub relative: u8,
}

impl MovePayload {
    pub const SIZE: usize = 9;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct KeyPayload {
    pub vkcode: u16,
    pub mods: u8,
}

impl KeyPayload {
    pub const SIZE: usize = 3;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct ScrollPayload {
    pub dx: i32,
    pub dy: i32,
}

impl ScrollPayload {
    pub const SIZE: usize = 8;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct DragPayload {
    pub x1: i32,
    pub y1: i32,
    pub x2: i32,
    pub y2: i32,
    pub button: u8,
}

impl DragPayload {
    pub const SIZE: usize = 17;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct ScreenshotPayload {
    pub monitor: u8,
}

impl ScreenshotPayload {
    pub const SIZE: usize = 1;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct StreamSubPayload {
    pub fps: u8,
    pub mode: u8, // 0 = full frames, 1 = diff only
}

impl StreamSubPayload {
    pub const SIZE: usize = 2;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct UiSnapshotPayload {
    pub flags: u8,
}

impl UiSnapshotPayload {
    pub const SIZE: usize = 1;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct UiClickLabelPayload {
    pub label_id: u32,
    pub button: u8,
}

impl UiClickLabelPayload {
    pub const SIZE: usize = 5;
}

#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct AckPayload {
    pub status: u8,
}

impl AckPayload {
    pub const SIZE: usize = 1;
}

// FRAME response header (variable tail follows: data bytes of length payload_len - 5)
#[repr(C, packed)]
#[derive(AsBytes, FromBytes, FromZeroes, Copy, Clone, Debug)]
pub struct FrameHeader {
    pub width: u16,
    pub height: u16,
    pub format: u8,
}

impl FrameHeader {
    pub const SIZE: usize = 5;
}

// =============================================================================
// Errors
// =============================================================================

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtoError {
    UnknownOpcode(u8),
    BadHeader,
    BadPayload,
    BadField(&'static str, u32),
    ShortBuffer { needed: usize, got: usize },
    PayloadTooLarge { len: u32, max: u32 },
}

impl core::fmt::Display for ProtoError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            ProtoError::UnknownOpcode(b) => write!(f, "unknown opcode 0x{b:02X}"),
            ProtoError::BadHeader => f.write_str("bad header"),
            ProtoError::BadPayload => f.write_str("bad payload"),
            ProtoError::BadField(name, v) => write!(f, "bad field {name}={v}"),
            ProtoError::ShortBuffer { needed, got } => {
                write!(f, "short buffer: need {needed}, got {got}")
            }
            ProtoError::PayloadTooLarge { len, max } => {
                write!(f, "payload too large: {len} > {max}")
            }
        }
    }
}

#[cfg(feature = "std")]
impl std::error::Error for ProtoError {}

// =============================================================================
// Helpers
// =============================================================================

/// Encode a complete frame (header + payload) into a single Vec<u8>.
#[cfg(feature = "std")]
pub fn encode_frame(op: Op, flags: Flags, seq: u16, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(Header::SIZE + payload.len());
    let hdr = Header::new(op, flags, seq, payload.len() as u32);
    out.extend_from_slice(hdr.as_bytes());
    out.extend_from_slice(payload);
    out
}

// =============================================================================
// Tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn header_size_is_8() {
        assert_eq!(Header::SIZE, 8);
        assert_eq!(core::mem::size_of::<Header>(), 8);
    }

    #[test]
    fn click_payload_size_is_10() {
        assert_eq!(ClickPayload::SIZE, 10);
        assert_eq!(core::mem::size_of::<ClickPayload>(), 10);
    }

    #[test]
    fn move_payload_size_is_9() {
        assert_eq!(MovePayload::SIZE, 9);
        assert_eq!(core::mem::size_of::<MovePayload>(), 9);
    }

    #[test]
    fn drag_payload_size_is_17() {
        assert_eq!(DragPayload::SIZE, 17);
        assert_eq!(core::mem::size_of::<DragPayload>(), 17);
    }

    #[test]
    fn header_roundtrip() {
        let hdr = Header::new(Op::Click, Flags::FIRE_FORGET, 0x1234, 10);
        let bytes = hdr.as_bytes();
        let parsed = Header::parse(bytes).unwrap();
        let parsed_op = parsed.op;
        let parsed_seq = parsed.seq;
        let parsed_len = parsed.payload_len;
        assert_eq!(parsed_op, Op::Click as u8);
        assert_eq!(parsed.flags(), Flags::FIRE_FORGET);
        assert_eq!(parsed_seq, 0x1234);
        assert_eq!(parsed_len, 10);
    }

    #[test]
    fn click_payload_roundtrip() {
        let p = ClickPayload { x: 100, y: 200, button: MouseButton::Left as u8, double: 0 };
        let bytes = p.as_bytes();
        let parsed = ClickPayload::read_from(bytes).unwrap();
        let x = parsed.x;
        let y = parsed.y;
        assert_eq!(x, 100);
        assert_eq!(y, 200);
        assert_eq!(parsed.button, MouseButton::Left as u8);
    }

    #[test]
    fn op_try_from() {
        assert_eq!(Op::try_from(0x10).unwrap(), Op::Click);
        assert_eq!(Op::try_from(0xFF).unwrap(), Op::Err);
        assert!(Op::try_from(0x77).is_err());
    }

    #[test]
    fn flags_bits() {
        let f = Flags::FIRE_FORGET | Flags::COMPRESS;
        assert_eq!(f.bits(), 0b0000_0110);
    }

    #[test]
    fn encode_frame_layout() {
        let payload = ClickPayload { x: 5, y: 6, button: 1, double: 0 };
        let frame = encode_frame(Op::Click, Flags::empty(), 1, payload.as_bytes());
        assert_eq!(frame.len(), Header::SIZE + ClickPayload::SIZE);
        assert_eq!(frame[0], Op::Click as u8);
    }
}
