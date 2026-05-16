use thiserror::Error;

use gargaros_protocol::ProtoError;

#[derive(Debug, Error)]
pub enum Error {
    #[error("protocol error: {0}")]
    Proto(#[from] ProtoError),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("input queue is full")]
    QueueFull,

    #[error("subsystem unavailable: {0}")]
    Unavailable(&'static str),

    #[error("ring buffer not initialised")]
    RingNotReady,

    #[error("payload too large: {got} bytes, max {max}")]
    PayloadTooLarge { got: usize, max: usize },

    #[error("windows error: {0}")]
    Windows(String),

    #[error("encode error: {0}")]
    Encode(String),

    #[error("other: {0}")]
    Other(String),
}

#[cfg(windows)]
impl From<windows::core::Error> for Error {
    fn from(e: windows::core::Error) -> Self {
        Error::Windows(format!("0x{:08X}: {}", e.code().0, e.message()))
    }
}

pub type Result<T> = std::result::Result<T, Error>;
