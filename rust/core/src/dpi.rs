//! Make the process per-monitor-v2 DPI aware so `SendInput` coordinates and
//! `GetSystemMetrics(SM_CX|CYVIRTUALSCREEN)` agree on HiDPI displays.
//!
//! Plan section 10 (*Pieges a eviter / DPI awareness*).

use windows::Win32::UI::HiDpi::{
    SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};

/// Best-effort: returns `Ok` whether or not the call succeeds because the
/// process may already have a DPI awareness baked into its manifest.
pub fn make_per_monitor_v2_aware() {
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    }
}
