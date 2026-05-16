//! UI tree + WinEventHook subsystem.
//!
//! - Keeps a cached snapshot of the focused window + recent events.
//! - Runs `SetWinEventHook(WINEVENT_OUTOFCONTEXT)` on its own thread so the
//!   hook callback drains into a `tokio::sync::broadcast::Sender<UiEvent>`.
//!
//! UIA tree caching is left as a future enhancement: the first cut tracks
//! window focus + dialog open/close which already gives Claude the context
//! it needs for the streaming hook (see plan section 5.2).

use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
use std::sync::Arc;
use std::thread;

use parking_lot::Mutex;
use tokio::sync::broadcast;
use windows::Win32::Foundation::{BOOL, HWND, LPARAM, WPARAM};
use windows::Win32::UI::Accessibility::{SetWinEventHook, UnhookWinEvent, HWINEVENTHOOK};
use windows::Win32::UI::WindowsAndMessaging::{
    DispatchMessageW, GetForegroundWindow, GetMessageW, GetWindowTextW, TranslateMessage,
    EVENT_OBJECT_CREATE, EVENT_OBJECT_DESTROY, EVENT_OBJECT_FOCUS, EVENT_SYSTEM_DIALOGEND,
    EVENT_SYSTEM_DIALOGSTART, EVENT_SYSTEM_FOREGROUND, MSG, WINEVENT_OUTOFCONTEXT,
    WINEVENT_SKIPOWNPROCESS,
};

use crate::error::{Error, Result};

#[derive(Clone, Debug)]
pub enum UiEvent {
    WindowFocus { hwnd: isize, title: String },
    DialogOpened { hwnd: isize, title: String },
    DialogClosed { hwnd: isize },
    WindowCreated { hwnd: isize, title: String },
    WindowDestroyed { hwnd: isize },
}

impl UiEvent {
    pub fn kind_byte(&self) -> u8 {
        use gargaros_protocol::EventKind::*;
        match self {
            UiEvent::WindowFocus { .. } => WindowFocus as u8,
            UiEvent::DialogOpened { .. } => DialogOpened as u8,
            UiEvent::DialogClosed { .. } => DialogClosed as u8,
            UiEvent::WindowCreated { .. } => WindowCreated as u8,
            UiEvent::WindowDestroyed { .. } => WindowDestroyed as u8,
        }
    }

    pub fn encode(&self) -> Vec<u8> {
        // Body: u8 kind, i64 hwnd, u16 title_len, utf8 title.
        let (hwnd, title) = match self {
            UiEvent::WindowFocus { hwnd, title }
            | UiEvent::DialogOpened { hwnd, title }
            | UiEvent::WindowCreated { hwnd, title } => (*hwnd, title.as_str()),
            UiEvent::DialogClosed { hwnd } | UiEvent::WindowDestroyed { hwnd } => (*hwnd, ""),
        };
        let bytes = title.as_bytes();
        let mlen = u16::try_from(bytes.len()).unwrap_or(u16::MAX);
        let take = mlen as usize;
        let mut out = Vec::with_capacity(1 + 8 + 2 + take);
        out.push(self.kind_byte());
        out.extend_from_slice(&(hwnd as i64).to_le_bytes());
        out.extend_from_slice(&mlen.to_le_bytes());
        out.extend_from_slice(&bytes[..take]);
        out
    }
}

/// Cached desktop snapshot served by `Op::UiSnapshot`.
#[derive(Clone, Debug, Default)]
pub struct UiSnapshot {
    pub focused_hwnd: isize,
    pub focused_title: String,
    pub active_dialog: Option<String>,
}

#[derive(Clone)]
pub struct UiHandle {
    inner: Arc<UiInner>,
}

struct UiInner {
    snapshot: Mutex<UiSnapshot>,
    events: broadcast::Sender<UiEvent>,
    _hook_thread_alive: AtomicBool,
}

impl UiHandle {
    pub fn subscribe(&self) -> broadcast::Receiver<UiEvent> {
        self.inner.events.subscribe()
    }

    pub fn snapshot(&self) -> UiSnapshot {
        self.inner.snapshot.lock().clone()
    }

    pub fn poll_foreground(&self) -> UiSnapshot {
        let (hwnd, title) = current_foreground();
        let mut snap = self.inner.snapshot.lock();
        snap.focused_hwnd = hwnd;
        snap.focused_title = title;
        snap.clone()
    }
}

pub struct UiThread;

impl UiThread {
    pub fn spawn() -> Result<UiHandle> {
        let (tx, _rx) = broadcast::channel(1024);
        let inner = Arc::new(UiInner {
            snapshot: Mutex::new(UiSnapshot::default()),
            events: tx.clone(),
            _hook_thread_alive: AtomicBool::new(true),
        });
        let inner_thread = Arc::clone(&inner);
        thread::Builder::new()
            .name("gargaros-ui".into())
            .spawn(move || {
                if let Err(e) = run_hook_thread(inner_thread) {
                    tracing::error!(error = %e, "UI thread crashed");
                }
            })
            .map_err(|e| Error::Other(format!("spawn ui thread: {e}")))?;
        // Prime the snapshot before returning.
        let (hwnd, title) = current_foreground();
        {
            let mut s = inner.snapshot.lock();
            s.focused_hwnd = hwnd;
            s.focused_title = title;
        }
        Ok(UiHandle { inner })
    }
}

// =============================================================================
// Hook thread internals
// =============================================================================

static EVENT_SINK: AtomicIsize = AtomicIsize::new(0); // *const UiInner cast to isize

fn run_hook_thread(inner: Arc<UiInner>) -> Result<()> {
    // SAFETY: we keep `inner` alive for the lifetime of the hook because the
    // Arc clone is moved into this thread and the thread loops forever.
    EVENT_SINK.store(Arc::as_ptr(&inner) as isize, Ordering::Release);

    let hooks = unsafe {
        [
            SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_FOREGROUND,
                None,
                Some(win_event_proc),
                0,
                0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
            ),
            SetWinEventHook(
                EVENT_SYSTEM_DIALOGSTART,
                EVENT_SYSTEM_DIALOGEND,
                None,
                Some(win_event_proc),
                0,
                0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
            ),
            SetWinEventHook(
                EVENT_OBJECT_CREATE,
                EVENT_OBJECT_DESTROY,
                None,
                Some(win_event_proc),
                0,
                0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
            ),
            SetWinEventHook(
                EVENT_OBJECT_FOCUS,
                EVENT_OBJECT_FOCUS,
                None,
                Some(win_event_proc),
                0,
                0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
            ),
        ]
    };

    // WinEvents fire on the thread that pumps a message loop with
    // WINEVENT_OUTOFCONTEXT, so we drive GetMessageW here.
    unsafe {
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, HWND(std::ptr::null_mut()), 0, 0).0 > 0 {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    for h in hooks.iter() {
        if !h.0.is_null() {
            unsafe {
                let _ = UnhookWinEvent(*h);
            }
        }
    }
    Ok(())
}

unsafe extern "system" fn win_event_proc(
    _hook: HWINEVENTHOOK,
    event: u32,
    hwnd: HWND,
    id_object: i32,
    _id_child: i32,
    _id_event_thread: u32,
    _dwms_event_time: u32,
) {
    if id_object != 0 {
        return;
    } // we only care about top-level windows
    let sink_ptr = EVENT_SINK.load(Ordering::Acquire) as *const UiInner;
    if sink_ptr.is_null() {
        return;
    }
    let inner = unsafe { &*sink_ptr };

    let title = window_title(hwnd);
    let hwnd_i = hwnd_to_isize(hwnd);

    let evt = match event {
        EVENT_SYSTEM_FOREGROUND | EVENT_OBJECT_FOCUS => {
            let mut snap = inner.snapshot.lock();
            snap.focused_hwnd = hwnd_i;
            snap.focused_title = title.clone();
            UiEvent::WindowFocus { hwnd: hwnd_i, title }
        }
        EVENT_SYSTEM_DIALOGSTART => {
            let mut snap = inner.snapshot.lock();
            snap.active_dialog = Some(title.clone());
            UiEvent::DialogOpened { hwnd: hwnd_i, title }
        }
        EVENT_SYSTEM_DIALOGEND => {
            let mut snap = inner.snapshot.lock();
            snap.active_dialog = None;
            UiEvent::DialogClosed { hwnd: hwnd_i }
        }
        EVENT_OBJECT_CREATE => UiEvent::WindowCreated { hwnd: hwnd_i, title },
        EVENT_OBJECT_DESTROY => UiEvent::WindowDestroyed { hwnd: hwnd_i },
        _ => return,
    };

    // Drop send errors silently (no subscribers is fine).
    let _ = inner.events.send(evt);
}

fn current_foreground() -> (isize, String) {
    unsafe {
        let hwnd = GetForegroundWindow();
        (hwnd_to_isize(hwnd), window_title(hwnd))
    }
}

fn window_title(hwnd: HWND) -> String {
    if hwnd.0.is_null() {
        return String::new();
    }
    let mut buf = [0u16; 256];
    let len = unsafe { GetWindowTextW(hwnd, &mut buf) };
    if len <= 0 {
        return String::new();
    }
    String::from_utf16_lossy(&buf[..len as usize])
}

#[inline]
fn hwnd_to_isize(hwnd: HWND) -> isize {
    hwnd.0 as isize
}

// Suppress unused-import warnings on optional types.
#[allow(dead_code)]
fn _unused(_wp: WPARAM, _lp: LPARAM, _b: BOOL) {}
