//! Capture thread.
//!
//! Runs a 60 Hz screen capture loop on a dedicated OS thread and publishes
//! each frame as BGRA into the shared ring buffer.
//!
//! Implementation: GDI `BitBlt` of the primary monitor. This is portable
//! (works inside RDP / RemoteApp / VMs) and fast enough for a first cut.
//! A future revision can swap in `Windows.Graphics.Capture` (WGC) for
//! protected-window support, per plan section 10.

use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::HWND;
use windows::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
    GetObjectW, ReleaseDC, SelectObject, BITMAP, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    DIB_RGB_COLORS, HBITMAP, HDC, SRCCOPY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

use crate::error::{Error, Result};
use crate::ring::Ring;

const TARGET_FPS: u32 = 60;
const FRAME_INTERVAL: Duration = Duration::from_micros(1_000_000 / TARGET_FPS as u64);

pub struct CaptureThread {
    _stop: Arc<std::sync::atomic::AtomicBool>,
}

impl CaptureThread {
    pub fn spawn(ring: Arc<Ring>) -> Result<Self> {
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stop_thread = Arc::clone(&stop);
        thread::Builder::new()
            .name("gargaros-capture".into())
            .spawn(move || {
                if let Err(e) = run(ring, stop_thread) {
                    tracing::error!(error = %e, "capture thread crashed");
                }
            })
            .map_err(|e| Error::Other(format!("spawn capture thread: {e}")))?;
        Ok(Self { _stop: stop })
    }
}

fn run(ring: Arc<Ring>, stop: Arc<std::sync::atomic::AtomicBool>) -> Result<()> {
    tracing::debug!("capture thread started, target {TARGET_FPS} fps");
    let mut next_deadline = Instant::now();
    while !stop.load(std::sync::atomic::Ordering::Relaxed) {
        match grab_bgra() {
            Ok((w, h, data)) => {
                let ts = nanos_now();
                if let Err(e) = ring.publish(&data, w, h, 0, ts) {
                    tracing::warn!(error = %e, "ring publish failed");
                }
            }
            Err(e) => {
                tracing::warn!(error = %e, "capture failed");
                thread::sleep(Duration::from_millis(50));
            }
        }
        next_deadline += FRAME_INTERVAL;
        let now = Instant::now();
        if next_deadline > now {
            thread::sleep(next_deadline - now);
        } else {
            // We fell behind; resync deadline to now so we don't busy-loop.
            next_deadline = now;
        }
    }
    Ok(())
}

/// One-shot grab of the virtual screen, returns (width, height, BGRA bytes).
pub fn grab_bgra() -> Result<(u32, u32, Vec<u8>)> {
    let x0 = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let y0 = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let w = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) }.max(1) as i32;
    let h = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) }.max(1) as i32;

    unsafe {
        let screen: HDC = GetDC(HWND(std::ptr::null_mut()));
        if screen.0.is_null() {
            return Err(Error::Other("GetDC failed".into()));
        }
        let mem: HDC = CreateCompatibleDC(screen);
        if mem.0.is_null() {
            ReleaseDC(HWND(std::ptr::null_mut()), screen);
            return Err(Error::Other("CreateCompatibleDC failed".into()));
        }
        // ReleaseDC returns i32 (BOOL-like); cleanup paths above ignore intentionally.
        let bmp: HBITMAP = CreateCompatibleBitmap(screen, w, h);
        if bmp.0.is_null() {
            let _ = DeleteDC(mem);
            ReleaseDC(HWND(std::ptr::null_mut()), screen);
            return Err(Error::Other("CreateCompatibleBitmap failed".into()));
        }
        let old = SelectObject(mem, bmp);
        if BitBlt(mem, 0, 0, w, h, screen, x0, y0, SRCCOPY).is_err() {
            SelectObject(mem, old);
            let _ = DeleteObject(bmp);
            let _ = DeleteDC(mem);
            ReleaseDC(HWND(std::ptr::null_mut()), screen);
            return Err(Error::Other("BitBlt failed".into()));
        }

        let mut bmp_info: BITMAP = std::mem::zeroed();
        GetObjectW(
            bmp,
            std::mem::size_of::<BITMAP>() as i32,
            Some(&mut bmp_info as *mut _ as *mut _),
        );

        let stride = (w as usize) * 4;
        let mut buf = vec![0u8; stride * h as usize];

        let mut bi: BITMAPINFO = std::mem::zeroed();
        bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi.bmiHeader.biWidth = w;
        bi.bmiHeader.biHeight = -h; // top-down
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB.0;

        let scanlines = GetDIBits(
            mem,
            bmp,
            0,
            h as u32,
            Some(buf.as_mut_ptr() as *mut _),
            &mut bi,
            DIB_RGB_COLORS,
        );

        SelectObject(mem, old);
        let _ = DeleteObject(bmp);
        let _ = DeleteDC(mem);
        ReleaseDC(HWND(std::ptr::null_mut()), screen);

        if scanlines == 0 {
            return Err(Error::Other("GetDIBits failed".into()));
        }
        Ok((w as u32, h as u32, buf))
    }
}

fn nanos_now() -> u64 {
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}
