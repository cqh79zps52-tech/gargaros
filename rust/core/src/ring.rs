//! Shared-memory ring buffer for zero-copy frame distribution.
//!
//! See plan section 4. Layout in memory (page-aligned):
//!
//! ```text
//! +-------------------------------+
//! | RingHeader (256 bytes padded) |
//! +-------------------------------+
//! | SlotMeta * SLOT_COUNT         |
//! +-------------------------------+
//! | slot 0 payload (slot_size B)  |
//! +-------------------------------+
//! | slot 1 payload                |
//! +-------------------------------+
//! ...
//! ```
//!
//! Writers use a seqlock (`seq` even = stable, odd = in-flight); readers
//! re-check `seq` before/after the read and retry on torn reads.
//!
//! The first cut allocates anonymous shared memory through
//! `CreateFileMappingW(INVALID_HANDLE_VALUE, ...)` so in-process readers
//! (the Tokio server) and out-of-process clients can both `MapViewOfFile`
//! the same backing page set.

use std::ffi::OsString;
use std::os::windows::ffi::OsStrExt;
use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows::Win32::System::Memory::{
    CreateFileMappingW, MapViewOfFile, UnmapViewOfFile, VirtualAlloc, VirtualFree,
    FILE_MAP_ALL_ACCESS, MEMORY_BASIC_INFORMATION, MEM_COMMIT, MEM_RELEASE, MEM_RESERVE,
    PAGE_READWRITE,
};

use crate::error::{Error, Result};

pub const MAGIC: u32 = u32::from_le_bytes(*b"GARG");
pub const VERSION: u32 = 1;
pub const DEFAULT_SLOT_COUNT: u32 = 4;
pub const DEFAULT_SLOT_SIZE: u32 = 16 * 1024 * 1024; // 16 MB; fits 4K BGRA + room

const HEADER_RESERVED: usize = 256;
const SLOT_ALIGN: usize = 4096;

/// Fixed-size header at offset 0 of the shared region.
#[repr(C, align(64))]
pub struct RingHeader {
    pub magic: u32,
    pub version: u32,
    pub slot_count: u32,
    pub slot_size: u32,
    pub header_total_size: u32, // header + all SlotMeta, payload start offset
    pub write_idx: AtomicU64,
    // SlotMeta entries immediately follow this struct, then payloads.
}

#[repr(C, align(64))]
pub struct SlotMeta {
    pub seq: AtomicU64,
    pub width: u32,
    pub height: u32,
    pub format: u32, // FrameFormat enum value
    pub payload_len: u32,
    pub timestamp_ns: u64,
    pub frame_hash: u64,
}

#[derive(Debug, Clone)]
pub struct Frame {
    pub seq: u64,
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub timestamp_ns: u64,
    pub frame_hash: u64,
    pub data: Vec<u8>,
}

pub struct Ring {
    base: *mut u8,
    total_bytes: usize,
    slot_count: u32,
    slot_size: u32,
    header_total_size: u32,
    backing: Backing,
}

unsafe impl Send for Ring {}
unsafe impl Sync for Ring {}

enum Backing {
    /// Process-private virtual memory (for in-process use and unit tests).
    Virtual,
    /// Shared mapping that other processes can attach to via the handle.
    FileMapping { handle: HANDLE },
}

impl Ring {
    /// Allocate a process-private ring (no other process can attach).
    pub fn create_anonymous(slot_count: u32, slot_size: u32) -> Result<Self> {
        let (total, header_total) = layout_sizes(slot_count, slot_size)?;
        let base = unsafe {
            VirtualAlloc(None, total, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE) as *mut u8
        };
        if base.is_null() {
            return Err(Error::Other("VirtualAlloc failed".into()));
        }
        let mut ring = Self {
            base,
            total_bytes: total,
            slot_count,
            slot_size,
            header_total_size: header_total as u32,
            backing: Backing::Virtual,
        };
        ring.init_header();
        Ok(ring)
    }

    /// Allocate a shared mapping under the given name (eg "Local\\gargaros-ring")
    /// so out-of-process clients can attach with `OpenFileMappingW`.
    pub fn create_shared(name: &str, slot_count: u32, slot_size: u32) -> Result<Self> {
        let (total, header_total) = layout_sizes(slot_count, slot_size)?;
        let wide: Vec<u16> = OsString::from(name).encode_wide().chain(Some(0)).collect();
        let handle = unsafe {
            CreateFileMappingW(
                INVALID_HANDLE_VALUE,
                None,
                PAGE_READWRITE,
                (total >> 32) as u32,
                (total & 0xFFFF_FFFF) as u32,
                PCWSTR(wide.as_ptr()),
            )?
        };
        let view = unsafe { MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, total) };
        if view.Value.is_null() {
            unsafe { CloseHandle(handle).ok() };
            return Err(Error::Other("MapViewOfFile failed".into()));
        }
        let mut ring = Self {
            base: view.Value as *mut u8,
            total_bytes: total,
            slot_count,
            slot_size,
            header_total_size: header_total as u32,
            backing: Backing::FileMapping { handle },
        };
        ring.init_header();
        Ok(ring)
    }

    fn init_header(&mut self) {
        unsafe {
            ptr::write_bytes(self.base, 0, self.total_bytes);
            let hdr = self.header_mut();
            hdr.magic = MAGIC;
            hdr.version = VERSION;
            hdr.slot_count = self.slot_count;
            hdr.slot_size = self.slot_size;
            hdr.header_total_size = self.header_total_size;
            hdr.write_idx = AtomicU64::new(0);
            for i in 0..self.slot_count {
                let m = self.slot_meta_mut(i);
                m.seq = AtomicU64::new(0);
                m.width = 0;
                m.height = 0;
                m.format = 0;
                m.payload_len = 0;
                m.timestamp_ns = 0;
                m.frame_hash = 0;
            }
        }
    }

    pub fn slot_count(&self) -> u32 {
        self.slot_count
    }
    pub fn slot_size(&self) -> u32 {
        self.slot_size
    }

    fn header(&self) -> &RingHeader {
        unsafe { &*(self.base as *const RingHeader) }
    }

    #[allow(clippy::mut_from_ref)]
    fn header_mut(&self) -> &mut RingHeader {
        unsafe { &mut *(self.base as *mut RingHeader) }
    }

    fn slot_meta(&self, idx: u32) -> &SlotMeta {
        debug_assert!(idx < self.slot_count);
        unsafe {
            let p = self.base.add(HEADER_RESERVED) as *const SlotMeta;
            &*p.add(idx as usize)
        }
    }

    #[allow(clippy::mut_from_ref)]
    fn slot_meta_mut(&self, idx: u32) -> &mut SlotMeta {
        debug_assert!(idx < self.slot_count);
        unsafe {
            let p = self.base.add(HEADER_RESERVED) as *mut SlotMeta;
            &mut *p.add(idx as usize)
        }
    }

    fn slot_payload(&self, idx: u32) -> *mut u8 {
        debug_assert!(idx < self.slot_count);
        unsafe {
            self.base
                .add(self.header_total_size as usize)
                .add((idx as usize) * (self.slot_size as usize))
        }
    }

    // ============== writer side ==============

    /// Write a new frame into the next slot. Bumps `write_idx` on success.
    pub fn publish(
        &self,
        bytes: &[u8],
        width: u32,
        height: u32,
        format: u32,
        timestamp_ns: u64,
    ) -> Result<()> {
        if bytes.len() > self.slot_size as usize {
            return Err(Error::PayloadTooLarge {
                got: bytes.len(),
                max: self.slot_size as usize,
            });
        }
        let hdr = self.header();
        let next_idx = hdr.write_idx.load(Ordering::Relaxed);
        let slot = (next_idx % self.slot_count as u64) as u32;
        let meta = self.slot_meta_mut(slot);

        // Mark slot as being written (odd seq).
        let prev_seq = meta.seq.load(Ordering::Relaxed);
        let writing_seq = (prev_seq | 1).wrapping_add(2) & !1u64 | 1;
        meta.seq.store(writing_seq, Ordering::Release);

        // Copy payload + meta.
        unsafe {
            ptr::copy_nonoverlapping(bytes.as_ptr(), self.slot_payload(slot), bytes.len());
        }
        meta.width = width;
        meta.height = height;
        meta.format = format;
        meta.payload_len = bytes.len() as u32;
        meta.timestamp_ns = timestamp_ns;
        meta.frame_hash = xxhash_rust::xxh3::xxh3_64(bytes);

        // Stabilise the slot (even seq).
        meta.seq.store(writing_seq.wrapping_add(1), Ordering::Release);
        hdr.write_idx.store(next_idx.wrapping_add(1), Ordering::Release);
        Ok(())
    }

    // ============== reader side ==============

    /// Read the most recently published frame using a seqlock retry loop.
    pub fn read_latest(&self) -> Option<Frame> {
        let hdr = self.header();
        for _ in 0..16 {
            let write_idx = hdr.write_idx.load(Ordering::Acquire);
            if write_idx == 0 {
                return None;
            }
            let slot = ((write_idx - 1) % self.slot_count as u64) as u32;
            let meta = self.slot_meta(slot);
            let seq_before = meta.seq.load(Ordering::Acquire);
            if seq_before & 1 == 1 {
                std::hint::spin_loop();
                continue;
            }
            let width = meta.width;
            let height = meta.height;
            let format = meta.format;
            let ts = meta.timestamp_ns;
            let hash = meta.frame_hash;
            let len = meta.payload_len as usize;
            let mut buf = vec![0u8; len];
            unsafe { ptr::copy_nonoverlapping(self.slot_payload(slot), buf.as_mut_ptr(), len) };
            let seq_after = meta.seq.load(Ordering::Acquire);
            if seq_after == seq_before {
                return Some(Frame {
                    seq: seq_before,
                    width,
                    height,
                    format,
                    timestamp_ns: ts,
                    frame_hash: hash,
                    data: buf,
                });
            }
        }
        None
    }
}

impl Drop for Ring {
    fn drop(&mut self) {
        unsafe {
            match self.backing {
                Backing::Virtual => {
                    VirtualFree(self.base as *mut _, 0, MEM_RELEASE).ok();
                }
                Backing::FileMapping { handle } => {
                    let view = windows::Win32::System::Memory::MEMORY_MAPPED_VIEW_ADDRESS {
                        Value: self.base as *mut _,
                    };
                    UnmapViewOfFile(view).ok();
                    CloseHandle(handle).ok();
                }
            }
        }
    }
}

fn layout_sizes(slot_count: u32, slot_size: u32) -> Result<(usize, usize)> {
    if slot_count == 0 {
        return Err(Error::Other("slot_count must be > 0".into()));
    }
    let slot_size = slot_size as usize;
    if !slot_size.is_multiple_of(SLOT_ALIGN) {
        return Err(Error::Other(format!("slot_size must be page-aligned (4096), got {slot_size}")));
    }
    let meta_total = std::mem::size_of::<SlotMeta>() * slot_count as usize;
    let header_total = align_up(HEADER_RESERVED + meta_total, SLOT_ALIGN);
    let total = header_total + slot_size * slot_count as usize;
    Ok((total, header_total))
}

fn align_up(value: usize, align: usize) -> usize {
    (value + align - 1) & !(align - 1)
}

// Suppress unused-import warnings on the few items we keep around for future use.
#[allow(dead_code)]
fn _unused_keepalive(mbi: MEMORY_BASIC_INFORMATION) -> MEMORY_BASIC_INFORMATION {
    mbi
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anonymous_ring_round_trip() {
        let ring = Ring::create_anonymous(4, 4096 * 4).unwrap();
        let payload = vec![0xABu8; 1024];
        ring.publish(&payload, 32, 32, 0, 123).unwrap();
        let frame = ring.read_latest().unwrap();
        assert_eq!(frame.data, payload);
        assert_eq!(frame.width, 32);
        assert_eq!(frame.height, 32);
        assert_eq!(frame.timestamp_ns, 123);
    }

    #[test]
    fn wraps_around_slots() {
        let ring = Ring::create_anonymous(2, 4096).unwrap();
        for i in 0..10u32 {
            let payload = vec![i as u8; 16];
            ring.publish(&payload, i, 0, 0, i as u64).unwrap();
        }
        let frame = ring.read_latest().unwrap();
        assert_eq!(frame.width, 9);
        assert_eq!(frame.data, vec![9u8; 16]);
    }

    #[test]
    fn rejects_payload_too_large() {
        let ring = Ring::create_anonymous(2, 4096).unwrap();
        let payload = vec![0u8; 8192];
        assert!(ring.publish(&payload, 1, 1, 0, 0).is_err());
    }
}
