//! `gargaros-server` daemon entry point.
//!
//! Starts every subsystem and serves the Named Pipe at `\\.\pipe\gargaros`.

use tracing_subscriber::EnvFilter;

#[cfg(windows)]
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    tracing::info!(version = env!("CARGO_PKG_VERSION"), "starting gargaros-server");

    let state = gargaros_core::State::spawn()?;
    gargaros_core::pipe::serve(state).await?;
    Ok(())
}

#[cfg(not(windows))]
fn main() {
    eprintln!("gargaros-server only supports Windows.");
    std::process::exit(1);
}
