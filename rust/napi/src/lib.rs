//! NAPI bindings for `gargaros-core`.
//!
//! Plan target (Section 8.3 of the design doc): expose `desktop_click`,
//! `desktop_type`, `desktop_key`, `desktop_screenshot` as native Node.js
//! functions so Claude Code can call them through `napi.call(...)` instead of
//! the Named Pipe.
//!
//! The first revision of this crate is a placeholder so the workspace builds
//! end-to-end; once `gargaros-core` is fully wired we'll re-introduce the
//! `napi`/`napi-derive` dependencies and the actual exports.

pub use gargaros_core as core;
pub use gargaros_protocol as protocol;

/// Crate version exposed for sanity checking from JS once bindings land.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
