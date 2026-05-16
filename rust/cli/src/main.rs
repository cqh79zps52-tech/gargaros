//! `gargaros` test CLI.
//!
//! Talks to the `\\.\pipe\gargaros` Named Pipe using the binary protocol.

use std::process::ExitCode;

use anyhow::{anyhow, Result};
use zerocopy::AsBytes;

use gargaros_protocol::{
    encode_frame, ClickPayload, Flags, KeyPayload, MovePayload, Op, ScreenshotPayload,
    ScrollPayload, StreamSubPayload,
};

#[cfg(windows)]
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::windows::named_pipe::ClientOptions,
};

const PIPE_NAME: &str = r"\\.\pipe\gargaros";

fn usage() -> ! {
    eprintln!(
        "usage: gargaros <command> [args]\n\n  ping\n  click <x> <y> [left|right|middle]\n  move <x> <y>\n  type <text>\n  key <vkcode>\n  scroll <dx> <dy>\n  screenshot [monitor]\n  stream <fps>"
    );
    std::process::exit(2);
}

#[cfg(windows)]
#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")))
        .init();

    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e:#}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(not(windows))]
fn main() -> ExitCode {
    eprintln!("gargaros CLI only supports Windows.");
    ExitCode::FAILURE
}

#[cfg(windows)]
async fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        usage();
    }
    let cmd = args[0].as_str();
    let mut client = ClientOptions::new()
        .open(PIPE_NAME)
        .map_err(|e| anyhow!("cannot open {PIPE_NAME}: {e}. Is gargaros-server running?"))?;

    match cmd {
        "ping" => {
            let frame = encode_frame(Op::Ping, Flags::ACK_REQUIRED, 1, &[]);
            client.write_all(&frame).await?;
            let (op, _seq, _payload) = read_one(&mut client).await?;
            println!("got opcode 0x{op:02X}");
        }
        "click" => {
            let x: i32 = args.get(1).ok_or_else(|| anyhow!("missing x"))?.parse()?;
            let y: i32 = args.get(2).ok_or_else(|| anyhow!("missing y"))?.parse()?;
            let btn = match args.get(3).map(|s| s.as_str()).unwrap_or("left") {
                "left" => 1u8,
                "right" => 2u8,
                "middle" => 3u8,
                other => return Err(anyhow!("unknown button {other}")),
            };
            let payload = ClickPayload { x, y, button: btn, double: 0 };
            let frame = encode_frame(Op::Click, Flags::FIRE_FORGET, 1, payload.as_bytes());
            client.write_all(&frame).await?;
        }
        "move" => {
            let x: i32 = args.get(1).ok_or_else(|| anyhow!("missing x"))?.parse()?;
            let y: i32 = args.get(2).ok_or_else(|| anyhow!("missing y"))?.parse()?;
            let payload = MovePayload { x, y, relative: 0 };
            let frame = encode_frame(Op::Move, Flags::FIRE_FORGET, 1, payload.as_bytes());
            client.write_all(&frame).await?;
        }
        "type" => {
            let text = args.get(1).ok_or_else(|| anyhow!("missing text"))?;
            let bytes = text.as_bytes();
            if bytes.len() > u16::MAX as usize {
                return Err(anyhow!("text too long"));
            }
            let len = bytes.len() as u16;
            let mut payload = Vec::with_capacity(2 + bytes.len() + 1);
            payload.extend_from_slice(&len.to_le_bytes());
            payload.extend_from_slice(bytes);
            payload.push(0u8); // enter = false
            let frame = encode_frame(Op::Type, Flags::FIRE_FORGET, 1, &payload);
            client.write_all(&frame).await?;
        }
        "key" => {
            let vk: u16 = args.get(1).ok_or_else(|| anyhow!("missing vk"))?.parse()?;
            let payload = KeyPayload { vkcode: vk, mods: 0 };
            let frame = encode_frame(Op::Key, Flags::FIRE_FORGET, 1, payload.as_bytes());
            client.write_all(&frame).await?;
        }
        "scroll" => {
            let dx: i32 = args.get(1).ok_or_else(|| anyhow!("missing dx"))?.parse()?;
            let dy: i32 = args.get(2).ok_or_else(|| anyhow!("missing dy"))?.parse()?;
            let payload = ScrollPayload { dx, dy };
            let frame = encode_frame(Op::Scroll, Flags::FIRE_FORGET, 1, payload.as_bytes());
            client.write_all(&frame).await?;
        }
        "screenshot" => {
            let mon: u8 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
            let payload = ScreenshotPayload { monitor: mon };
            let frame = encode_frame(Op::ScreenshotNow, Flags::ACK_REQUIRED, 1, payload.as_bytes());
            client.write_all(&frame).await?;
            let (op, _seq, body) = read_one(&mut client).await?;
            eprintln!("frame received: opcode 0x{op:02X}, {} bytes", body.len());
        }
        "stream" => {
            let fps: u8 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(10);
            let payload = StreamSubPayload { fps, mode: 1 };
            let frame = encode_frame(Op::StreamSub, Flags::empty(), 1, payload.as_bytes());
            client.write_all(&frame).await?;
            eprintln!("streaming, ctrl-c to stop");
            loop {
                let (op, _seq, body) = read_one(&mut client).await?;
                eprintln!("op 0x{op:02X}, {} bytes", body.len());
            }
        }
        _ => usage(),
    }

    Ok(())
}

#[cfg(windows)]
async fn read_one(
    client: &mut tokio::net::windows::named_pipe::NamedPipeClient,
) -> Result<(u8, u16, Vec<u8>)> {
    let mut hdr = [0u8; 8];
    client.read_exact(&mut hdr).await?;
    let op = hdr[0];
    let seq = u16::from_le_bytes([hdr[2], hdr[3]]);
    let plen = u32::from_le_bytes([hdr[4], hdr[5], hdr[6], hdr[7]]) as usize;
    let mut body = vec![0u8; plen];
    if plen > 0 {
        client.read_exact(&mut body).await?;
    }
    Ok((op, seq, body))
}
