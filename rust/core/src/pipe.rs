//! Tokio Named-Pipe server.
//!
//! Pipe: `\\.\pipe\gargaros`, `PIPE_TYPE_MESSAGE` so the kernel handles
//! framing. Each client gets one Tokio task; the per-task loop reads one
//! binary frame at a time and dispatches to the right subsystem.
//!
//! Heavy work (`SendInput`, image encoding) is owned by background threads;
//! the Tokio task only marshals.

use std::sync::Arc;
use std::time::Duration;

use tokio::net::windows::named_pipe::{NamedPipeServer, PipeMode, ServerOptions};
use tokio::time::timeout;
use zerocopy::FromBytes;

use gargaros_protocol::{
    ClickPayload, DragPayload, ErrCode, Flags, KeyPayload, MouseButton, MovePayload, Op,
    ScreenshotPayload, ScrollPayload, StreamSubPayload,
};

use crate::error::Result;
use crate::input::Action;
use crate::proto_io::{read_frame, write_ack, write_err, write_frame, write_pong};
use crate::ring::Ring;
use crate::State;

pub const PIPE_NAME: &str = r"\\.\pipe\gargaros";

pub async fn serve(state: Arc<State>) -> Result<()> {
    tracing::info!(pipe = PIPE_NAME, "Named Pipe server listening");
    let mut server = ServerOptions::new()
        .first_pipe_instance(true)
        .pipe_mode(PipeMode::Message)
        .create(PIPE_NAME)?;

    loop {
        server.connect().await?;
        let conn = server;
        server = ServerOptions::new().pipe_mode(PipeMode::Message).create(PIPE_NAME)?;
        let state = Arc::clone(&state);
        tokio::spawn(async move {
            if let Err(e) = handle_client(conn, state).await {
                tracing::warn!(error = %e, "client task ended");
            }
        });
    }
}

async fn handle_client(mut conn: NamedPipeServer, state: Arc<State>) -> Result<()> {
    loop {
        let (hdr, payload) = match read_frame(&mut conn).await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!(error = %e, "client disconnect");
                return Ok(());
            }
        };
        let seq = hdr.seq;
        let op = match Op::try_from(hdr.op) {
            Ok(o) => o,
            Err(_) => {
                write_err(&mut conn, seq, ErrCode::Unimplemented, "unknown opcode").await?;
                continue;
            }
        };
        let flags = hdr.flags();
        let fire_forget = flags.contains(Flags::FIRE_FORGET);
        if let Err(e) = dispatch(&mut conn, seq, op, &payload, &state, fire_forget).await {
            tracing::warn!(opcode = ?op, error = %e, "dispatch error");
            let _ = write_err(&mut conn, seq, ErrCode::Internal, &e.to_string()).await;
        }
    }
}

async fn dispatch(
    conn: &mut NamedPipeServer,
    seq: u16,
    op: Op,
    payload: &[u8],
    state: &State,
    fire_forget: bool,
) -> Result<()> {
    match op {
        Op::Ping => write_pong(conn, seq).await?,

        Op::Click => {
            let p = ClickPayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad click payload".into()))?;
            let button = MouseButton::try_from(p.button)?;
            state.input.submit(Action::Click {
                x: p.x,
                y: p.y,
                button,
                double: p.double != 0,
            })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::Move => {
            let p = MovePayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad move payload".into()))?;
            state.input.submit(Action::Move { x: p.x, y: p.y, relative: p.relative != 0 })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::Type => {
            if payload.len() < 3 {
                return Err(crate::error::Error::Other("type payload too small".into()));
            }
            let len = u16::from_le_bytes([payload[0], payload[1]]) as usize;
            if payload.len() < 2 + len + 1 {
                return Err(crate::error::Error::Other("type payload truncated".into()));
            }
            let text = std::str::from_utf8(&payload[2..2 + len])
                .map_err(|e| crate::error::Error::Other(format!("type utf8: {e}")))?
                .to_owned();
            let press_enter = payload[2 + len] != 0;
            state.input.submit(Action::Type { text, press_enter })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::Key => {
            let p = KeyPayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad key payload".into()))?;
            state.input.submit(Action::Key {
                vk: p.vkcode,
                mods: p.mods,
                down_only: false,
                up_only: false,
            })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::Scroll => {
            let p = ScrollPayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad scroll payload".into()))?;
            state.input.submit(Action::Scroll { dx: p.dx, dy: p.dy })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::Drag => {
            let p = DragPayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad drag payload".into()))?;
            let button = MouseButton::try_from(p.button)?;
            state.input.submit(Action::Drag {
                x1: p.x1,
                y1: p.y1,
                x2: p.x2,
                y2: p.y2,
                button,
            })?;
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::ScreenshotNow => {
            let _p = ScreenshotPayload::read_from(payload);
            let frame = wait_for_frame(&state.ring, Duration::from_millis(500)).await?;
            let body = encode_frame_response(&frame).await?;
            write_frame(conn, Op::Frame, Flags::empty(), seq, &body).await?;
        }

        Op::StreamSub => {
            let p = StreamSubPayload::read_from(payload)
                .ok_or_else(|| crate::error::Error::Other("bad stream payload".into()))?;
            let mut rx = state.stream.subscribe(p.fps, p.mode != 0);
            while let Some(body) = rx.recv().await {
                if let Err(e) = write_frame(conn, Op::Frame, Flags::empty(), 0, &body).await {
                    tracing::debug!(error = %e, "stream client gone");
                    break;
                }
            }
        }

        Op::StreamUnsub => {
            // The current implementation lets the per-subscription mpsc rx
            // close naturally when the client disconnects. Acknowledge so the
            // caller can confirm receipt.
            if !fire_forget {
                write_ack(conn, seq, 0).await?;
            }
        }

        Op::UiSnapshot => {
            let snap = state.ui.poll_foreground();
            let body = encode_ui_snapshot(&snap);
            write_frame(conn, Op::UiTree, Flags::empty(), seq, &body).await?;
        }

        Op::UiClickLabel => {
            // Future work: lookup label_id in the cached UIA tree, send a
            // synthetic SendInput at its bounding box centre.
            write_err(conn, seq, ErrCode::Unimplemented, "ui_click_label").await?;
        }

        Op::FindText => {
            write_err(conn, seq, ErrCode::Unimplemented, "find_text").await?;
        }

        // Response-only opcodes should never be received from the client.
        Op::Ack
        | Op::Pong
        | Op::Frame
        | Op::FrameDiff
        | Op::UiTree
        | Op::Matches
        | Op::Event
        | Op::Err => {
            write_err(conn, seq, ErrCode::Generic, "server-only opcode").await?;
        }
    }
    Ok(())
}

async fn wait_for_frame(ring: &Ring, max_wait: Duration) -> Result<crate::ring::Frame> {
    let deadline = tokio::time::Instant::now() + max_wait;
    loop {
        if let Some(frame) = ring.read_latest() {
            return Ok(frame);
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(crate::error::Error::RingNotReady);
        }
        let _ = timeout(Duration::from_millis(10), tokio::task::yield_now()).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

async fn encode_frame_response(frame: &crate::ring::Frame) -> Result<Vec<u8>> {
    let (w, h) = (frame.width, frame.height);
    let body = tokio::task::spawn_blocking({
        let data = frame.data.clone();
        move || -> Result<Vec<u8>> { crate::stream::encode_frame_body(&data, w, h) }
    })
    .await
    .map_err(|e| crate::error::Error::Other(format!("encode join: {e}")))??;
    Ok(body)
}

fn encode_ui_snapshot(snap: &crate::ui::UiSnapshot) -> Vec<u8> {
    // node_count: u32 = 1 (just the focused window for now)
    // node: i64 hwnd, u16 title_len, utf8 title, u16 dialog_len, utf8 dialog
    let title = snap.focused_title.as_bytes();
    let dialog = snap.active_dialog.as_deref().unwrap_or("").as_bytes();
    let tl = u16::try_from(title.len()).unwrap_or(u16::MAX);
    let dl = u16::try_from(dialog.len()).unwrap_or(u16::MAX);
    let mut out = Vec::with_capacity(4 + 8 + 2 + title.len() + 2 + dialog.len());
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(snap.focused_hwnd as i64).to_le_bytes());
    out.extend_from_slice(&tl.to_le_bytes());
    out.extend_from_slice(&title[..tl as usize]);
    out.extend_from_slice(&dl.to_le_bytes());
    out.extend_from_slice(&dialog[..dl as usize]);
    out
}
