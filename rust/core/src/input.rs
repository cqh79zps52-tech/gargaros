//! Dedicated input thread.
//!
//! All `SendInput` calls happen on a single OS thread so we never block the
//! Tokio scheduler (`SendInput` syscalls take 50-200us). The Tokio side talks
//! to it through a [`crossbeam_channel::Sender`].
//!
//! Text input uses `KEYEVENTF_UNICODE` so it works across keyboard layouts
//! (AZERTY, JIS, ...). See plan section 10 ("Pieges a eviter").

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use crossbeam_channel::{bounded, Receiver, Sender, TrySendError};
use gargaros_protocol::MouseButton;
use windows::Win32::Foundation::POINT;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE, MOUSEEVENTF_MOVE_NOCOALESCE, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL, MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, MOUSEINPUT,
    MOUSE_EVENT_FLAGS, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

// SendInput XButton ids (wParam high word). Defined in WinUser.h.
const XBUTTON1: u32 = 0x0001;
const XBUTTON2: u32 = 0x0002;

use crate::error::{Error, Result};

const QUEUE_CAPACITY: usize = 4096;
const DEFAULT_KEY_PAUSE: Duration = Duration::from_micros(0);

/// One mouse/keyboard action queued for the input thread.
#[derive(Debug, Clone)]
pub enum Action {
    Click { x: i32, y: i32, button: MouseButton, double: bool },
    Move { x: i32, y: i32, relative: bool },
    Type { text: String, press_enter: bool },
    Key { vk: u16, mods: u8, down_only: bool, up_only: bool },
    Scroll { dx: i32, dy: i32 },
    Drag { x1: i32, y1: i32, x2: i32, y2: i32, button: MouseButton },
}

bitflags::bitflags! {
    #[derive(Copy, Clone, Debug)]
    pub struct KeyMods: u8 {
        const SHIFT = 0b0000_0001;
        const CTRL  = 0b0000_0010;
        const ALT   = 0b0000_0100;
        const WIN   = 0b0000_1000;
    }
}

#[derive(Clone)]
pub struct InputHandle {
    tx: Sender<Action>,
    _shutdown: Arc<ShutdownGuard>,
}

impl InputHandle {
    pub fn submit(&self, action: Action) -> Result<()> {
        match self.tx.try_send(action) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => Err(Error::QueueFull),
            Err(TrySendError::Disconnected(_)) => Err(Error::Unavailable("input thread")),
        }
    }
}

struct ShutdownGuard {
    tx: Sender<Action>,
}

impl Drop for ShutdownGuard {
    fn drop(&mut self) {
        // Dropping the last sender disconnects the channel, which exits the loop.
        // We intentionally do nothing else here.
        let _ = &self.tx;
    }
}

pub struct InputThread;

impl InputThread {
    pub fn spawn() -> Result<InputHandle> {
        let (tx, rx) = bounded::<Action>(QUEUE_CAPACITY);
        let handle_tx = tx.clone();
        thread::Builder::new()
            .name("gargaros-input".into())
            .spawn(move || run(rx))
            .map_err(|e| Error::Other(format!("spawn input thread: {e}")))?;
        Ok(InputHandle { tx: handle_tx, _shutdown: Arc::new(ShutdownGuard { tx }) })
    }
}

fn run(rx: Receiver<Action>) {
    tracing::debug!("input thread started");
    for action in rx {
        if let Err(e) = dispatch(action) {
            tracing::warn!(error = %e, "input dispatch failed");
        }
    }
    tracing::debug!("input thread exiting");
}

fn dispatch(action: Action) -> Result<()> {
    match action {
        Action::Click { x, y, button, double } => click(x, y, button, double),
        Action::Move { x, y, relative } => move_cursor(x, y, relative),
        Action::Type { text, press_enter } => type_text(&text, press_enter),
        Action::Key { vk, mods, down_only, up_only } => {
            key_event(vk, KeyMods::from_bits_truncate(mods), down_only, up_only)
        }
        Action::Scroll { dx, dy } => scroll(dx, dy),
        Action::Drag { x1, y1, x2, y2, button } => drag(x1, y1, x2, y2, button),
    }
}

// =============================================================================
// SendInput helpers
// =============================================================================

fn send(inputs: &[INPUT]) -> Result<()> {
    let n = inputs.len() as u32;
    let sent = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent != n {
        return Err(Error::Other(format!("SendInput sent {sent}/{n}")));
    }
    Ok(())
}

fn mouse_event(flags: MOUSE_EVENT_FLAGS, dx: i32, dy: i32, data: i32) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy,
                mouseData: data as u32,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

fn kb_event(vk: u16, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

fn absolute_coords(x: i32, y: i32) -> (i32, i32) {
    // `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` expects normalised
    // coordinates in [0, 65535] across the **virtual** screen, with the
    // origin at `(SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN)` (which can be
    // negative when secondary monitors sit above/left of the primary).
    let x0 = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let y0 = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let w = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) }.max(1);
    let h = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) }.max(1);
    let nx = (((x - x0) as f64) * 65535.0 / (w as f64 - 1.0)).round() as i32;
    let ny = (((y - y0) as f64) * 65535.0 / (h as f64 - 1.0)).round() as i32;
    (nx.clamp(0, 65535), ny.clamp(0, 65535))
}

fn button_flags(button: MouseButton) -> (MOUSE_EVENT_FLAGS, MOUSE_EVENT_FLAGS, i32) {
    match button {
        MouseButton::Left => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
        MouseButton::Right => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
        MouseButton::Middle => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
        MouseButton::X1 => (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1 as i32),
        MouseButton::X2 => (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2 as i32),
    }
}

pub fn move_cursor(x: i32, y: i32, relative: bool) -> Result<()> {
    let event = if relative {
        mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE, x, y, 0)
    } else {
        let (nx, ny) = absolute_coords(x, y);
        mouse_event(
            MOUSEEVENTF_ABSOLUTE
                | MOUSEEVENTF_VIRTUALDESK
                | MOUSEEVENTF_MOVE
                | MOUSEEVENTF_MOVE_NOCOALESCE,
            nx,
            ny,
            0,
        )
    };
    send(&[event])
}

pub fn click(x: i32, y: i32, button: MouseButton, double: bool) -> Result<()> {
    move_cursor(x, y, false)?;
    let (down, up, data) = button_flags(button);
    let down_evt = mouse_event(down, 0, 0, data);
    let up_evt = mouse_event(up, 0, 0, data);
    if double {
        send(&[down_evt, up_evt, down_evt, up_evt])
    } else {
        send(&[down_evt, up_evt])
    }
}

pub fn scroll(dx: i32, dy: i32) -> Result<()> {
    let mut events = Vec::with_capacity(2);
    if dy != 0 {
        events.push(mouse_event(MOUSEEVENTF_WHEEL, 0, 0, dy * 120));
    }
    if dx != 0 {
        events.push(mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, dx * 120));
    }
    if events.is_empty() {
        return Ok(());
    }
    send(&events)
}

pub fn drag(x1: i32, y1: i32, x2: i32, y2: i32, button: MouseButton) -> Result<()> {
    move_cursor(x1, y1, false)?;
    let (down, up, data) = button_flags(button);
    send(&[mouse_event(down, 0, 0, data)])?;
    // small move to let target see the press before relocating
    let (nx, ny) = absolute_coords(x2, y2);
    let mv = mouse_event(
        MOUSEEVENTF_ABSOLUTE
            | MOUSEEVENTF_VIRTUALDESK
            | MOUSEEVENTF_MOVE
            | MOUSEEVENTF_MOVE_NOCOALESCE,
        nx,
        ny,
        0,
    );
    send(&[mv])?;
    send(&[mouse_event(up, 0, 0, data)])
}

/// Type a UTF-8 string using `KEYEVENTF_UNICODE`. Supports any code point in
/// the BMP; surrogate pairs are emitted as two events.
pub fn type_text(text: &str, press_enter: bool) -> Result<()> {
    for ch in text.chars() {
        for unit in encode_utf16(ch) {
            send(&[
                kb_event(0, unit, KEYEVENTF_UNICODE),
                kb_event(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            ])?;
            if DEFAULT_KEY_PAUSE > Duration::ZERO {
                thread::sleep(DEFAULT_KEY_PAUSE);
            }
        }
    }
    if press_enter {
        const VK_RETURN: u16 = 0x0D;
        let scan = unsafe {
            windows::Win32::UI::Input::KeyboardAndMouse::MapVirtualKeyW(
                VK_RETURN as u32,
                windows::Win32::UI::Input::KeyboardAndMouse::MAPVK_VK_TO_VSC,
            ) as u16
        };
        send(&[
            kb_event(VK_RETURN, scan, KEYBD_EVENT_FLAGS(0)),
            kb_event(VK_RETURN, scan, KEYEVENTF_KEYUP),
        ])?;
    }
    Ok(())
}

fn encode_utf16(ch: char) -> Vec<u16> {
    let mut tmp = [0u16; 2];
    ch.encode_utf16(&mut tmp).to_vec()
}

pub fn key_event(vk: u16, mods: KeyMods, down_only: bool, up_only: bool) -> Result<()> {
    let mut events: Vec<INPUT> = Vec::with_capacity(8);
    let mods_seq = [
        (mods.contains(KeyMods::SHIFT), 0x10u16),
        (mods.contains(KeyMods::CTRL), 0x11u16),
        (mods.contains(KeyMods::ALT), 0x12u16),
        (mods.contains(KeyMods::WIN), 0x5Bu16),
    ];

    if !up_only {
        for (active, mvk) in mods_seq {
            if active {
                events.push(kb_event(mvk, scan_for_vk(mvk), KEYBD_EVENT_FLAGS(0)));
            }
        }
        events.push(kb_event(vk, scan_for_vk(vk), KEYBD_EVENT_FLAGS(0)));
    }
    if !down_only {
        events.push(kb_event(vk, scan_for_vk(vk), KEYEVENTF_KEYUP));
        for (active, mvk) in mods_seq.iter().rev() {
            if *active {
                events.push(kb_event(*mvk, scan_for_vk(*mvk), KEYEVENTF_KEYUP));
            }
        }
    }
    if events.is_empty() {
        return Ok(());
    }
    send(&events)
}

fn scan_for_vk(vk: u16) -> u16 {
    use windows::Win32::UI::Input::KeyboardAndMouse::{MapVirtualKeyW, MAPVK_VK_TO_VSC};
    unsafe { MapVirtualKeyW(vk as u32, MAPVK_VK_TO_VSC) as u16 }
}

// =============================================================================
// Cursor query (used by tests and the UI tree subsystem)
// =============================================================================

pub fn cursor_pos() -> Result<(i32, i32)> {
    use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
    let mut p = POINT::default();
    unsafe { GetCursorPos(&mut p)? };
    Ok((p.x, p.y))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_mods_bits() {
        let m = KeyMods::SHIFT | KeyMods::CTRL;
        assert_eq!(m.bits(), 0b0000_0011);
    }

    #[test]
    fn encode_ascii_is_single_unit() {
        let units = encode_utf16('A');
        assert_eq!(units, vec![0x0041]);
    }

    #[test]
    fn encode_emoji_uses_surrogates() {
        // U+1F600 -> D83D DE00
        let units = encode_utf16('\u{1F600}');
        assert_eq!(units.len(), 2);
    }
}
