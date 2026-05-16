//! Frame streaming broker.
//!
//! `STREAM_SUB { fps, mode }` opens a per-client task that reads the latest
//! frame from the [`Ring`], resizes it down to <=1568px wide (vision-pipeline
//! sweet spot), encodes it as WebP and writes a `FRAME` (0x90) message.
//!
//! Mode 1 (`diff`) avoids re-sending unchanged frames by comparing the frame
//! hash from the ring meta. A full WGC-style dirty-rect diff stream is left
//! for a follow-up; the current "diff" mode is hash-gated full frames.

use std::sync::Arc;
use std::time::{Duration, Instant};

use fast_image_resize as fr;
use tokio::sync::mpsc;

use gargaros_protocol::FrameFormat;

use crate::error::{Error, Result};
use crate::ring::Ring;

pub const MAX_WIDTH: u32 = 1568;
pub const DEFAULT_FPS: u8 = 10;
pub const WEBP_QUALITY: f32 = 70.0;

#[derive(Clone)]
pub struct StreamBroker {
    ring: Arc<Ring>,
}

impl StreamBroker {
    pub fn new(ring: Arc<Ring>) -> Self {
        Self { ring }
    }

    /// Returns a receiver that yields encoded FRAME bodies (with FrameHeader
    /// prefix) ready to be written by the Named-Pipe task with opcode 0x90.
    pub fn subscribe(&self, fps: u8, diff_only: bool) -> mpsc::Receiver<Vec<u8>> {
        let fps = fps.max(1) as u32;
        let period = Duration::from_micros(1_000_000 / fps as u64);
        let (tx, rx) = mpsc::channel::<Vec<u8>>(8);
        let ring = Arc::clone(&self.ring);
        tokio::spawn(async move {
            let mut last_hash: u64 = 0;
            let mut next = Instant::now();
            loop {
                if tx.is_closed() {
                    break;
                }
                let now = Instant::now();
                if now < next {
                    tokio::time::sleep(next - now).await;
                }
                next += period;

                let Some(frame) = ring.read_latest() else { continue };
                if diff_only && frame.frame_hash == last_hash {
                    continue;
                }
                let body = match encode_frame_body(&frame.data, frame.width, frame.height) {
                    Ok(body) => body,
                    Err(e) => {
                        tracing::warn!(error = %e, "encode failed");
                        continue;
                    }
                };
                last_hash = frame.frame_hash;
                if tx.send(body).await.is_err() {
                    break;
                }
            }
        });
        rx
    }
}

/// Resize BGRA -> RGBA, encode as WebP, prefix with FrameHeader.
///
/// Public so the Named-Pipe `ScreenshotNow` handler can reuse the exact same
/// path as the streaming subscription.
pub fn encode_frame_body(bgra: &[u8], width: u32, height: u32) -> Result<Vec<u8>> {
    let (out_w, out_h) = scaled_size(width, height, MAX_WIDTH);
    let rgba = bgra_to_rgba(bgra, width, height)?;

    if width == 0 || height == 0 || out_w == 0 || out_h == 0 {
        return Err(Error::Other("zero dimension".into()));
    }
    let src = fr::images::Image::from_vec_u8(width, height, rgba, fr::pixels::PixelType::U8x4)
        .map_err(|e| Error::Encode(format!("source image: {e}")))?;

    let mut dst = fr::images::Image::new(out_w, out_h, fr::pixels::PixelType::U8x4);

    let mut resizer = fr::Resizer::new();
    resizer
        .resize(&src, &mut dst, None)
        .map_err(|e| Error::Encode(format!("resize: {e}")))?;

    let encoder = webp::Encoder::from_rgba(dst.buffer(), out_w, out_h);
    let webp_bytes = encoder.encode(WEBP_QUALITY).to_vec();

    Ok(crate::proto_io::build_frame_body(
        u16::try_from(out_w).unwrap_or(u16::MAX),
        u16::try_from(out_h).unwrap_or(u16::MAX),
        FrameFormat::WebP as u8,
        &webp_bytes,
    ))
}

fn scaled_size(w: u32, h: u32, max_w: u32) -> (u32, u32) {
    if w <= max_w {
        return (w, h);
    }
    let scale = max_w as f64 / w as f64;
    (max_w, ((h as f64) * scale).round().max(1.0) as u32)
}

fn bgra_to_rgba(bgra: &[u8], w: u32, h: u32) -> Result<Vec<u8>> {
    let expected = (w as usize) * (h as usize) * 4;
    if bgra.len() != expected {
        return Err(Error::Encode(format!(
            "bgra size mismatch: have {}, expect {expected}",
            bgra.len()
        )));
    }
    let mut out = Vec::with_capacity(bgra.len());
    for px in bgra.chunks_exact(4) {
        out.extend_from_slice(&[px[2], px[1], px[0], px[3]]);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scales_down_above_max() {
        assert_eq!(scaled_size(3840, 2160, 1568), (1568, 882));
    }

    #[test]
    fn keeps_size_below_max() {
        assert_eq!(scaled_size(1024, 768, 1568), (1024, 768));
    }
}
