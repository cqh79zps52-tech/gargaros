//! Gargaros core library.
//!
//! Three threads run independently behind a small set of channels and a
//! shared-memory ring buffer:
//!
//!   - `input`     : consumes a lock-free queue and calls `SendInput`.
//!   - `capture`   : runs a 60 Hz screen capture loop and writes BGRA frames
//!     into a seqlock-protected ring buffer.
//!   - `ui`        : maintains a UI Automation tree cache plus a stream of
//!     `WinEventHook` events (focus changes, dialogs, ...).
//!
//! A Tokio Named-Pipe server (`pipe`) parses the binary protocol from
//! [`gargaros_protocol`] and dispatches to those subsystems.

#![allow(clippy::missing_safety_doc)]

pub mod error;

#[cfg(windows)]
pub mod dpi;
#[cfg(windows)]
pub mod input;
#[cfg(windows)]
pub mod ring;
#[cfg(windows)]
pub mod capture;
#[cfg(windows)]
pub mod proto_io;
#[cfg(windows)]
pub mod pipe;
#[cfg(windows)]
pub mod ui;
#[cfg(windows)]
pub mod stream;

use std::sync::Arc;

pub use error::{Error, Result};
pub use gargaros_protocol as protocol;

/// Shared state passed to every Named-Pipe client task.
#[cfg(windows)]
pub struct State {
    pub input: input::InputHandle,
    pub ring: Arc<ring::Ring>,
    pub ui: ui::UiHandle,
    pub stream: stream::StreamBroker,
}

#[cfg(windows)]
impl State {
    /// Spin up every subsystem. Returns the `State` once everything is ready.
    pub fn spawn() -> Result<Arc<Self>> {
        // SendInput coordinates and capture metrics only line up on HiDPI
        // displays if the process opts in to per-monitor V2 awareness.
        dpi::make_per_monitor_v2_aware();
        let input = input::InputThread::spawn()?;
        let ring = ring::Ring::create_anonymous(ring::DEFAULT_SLOT_COUNT, ring::DEFAULT_SLOT_SIZE)?;
        let ring = Arc::new(ring);
        let _capture = capture::CaptureThread::spawn(Arc::clone(&ring))?;
        let ui = ui::UiThread::spawn()?;
        let stream = stream::StreamBroker::new(Arc::clone(&ring));
        Ok(Arc::new(Self { input, ring, ui, stream }))
    }
}
