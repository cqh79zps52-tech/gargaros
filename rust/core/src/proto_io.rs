//! Async helpers to read/write a single Gargaros wire frame from any
//! `tokio::io::AsyncRead + AsyncWrite` transport (Named Pipe, TCP, ...).

use tokio::io::{AsyncReadExt, AsyncWrite, AsyncWriteExt};
use zerocopy::AsBytes;

use gargaros_protocol::{
    AckPayload, ErrCode, Flags, FrameHeader, Header, Op, ProtoError, MAX_PAYLOAD_LEN,
};

use crate::error::{Error, Result};

/// Read one complete frame (header + payload) from `reader`.
pub async fn read_frame<R: tokio::io::AsyncRead + Unpin>(
    reader: &mut R,
) -> Result<(Header, Vec<u8>)> {
    let mut hdr_buf = [0u8; Header::SIZE];
    reader.read_exact(&mut hdr_buf).await?;
    let hdr = Header::parse(&hdr_buf)?;
    let len = hdr.payload_len;
    if len > MAX_PAYLOAD_LEN {
        return Err(Error::Proto(ProtoError::PayloadTooLarge { len, max: MAX_PAYLOAD_LEN }));
    }
    let mut body = vec![0u8; len as usize];
    if len > 0 {
        reader.read_exact(&mut body).await?;
    }
    Ok((hdr, body))
}

/// Write a header + payload pair to `writer` in a single buffered call.
pub async fn write_frame<W: AsyncWrite + Unpin>(
    writer: &mut W,
    op: Op,
    flags: Flags,
    seq: u16,
    payload: &[u8],
) -> Result<()> {
    if payload.len() > MAX_PAYLOAD_LEN as usize {
        return Err(Error::PayloadTooLarge {
            got: payload.len(),
            max: MAX_PAYLOAD_LEN as usize,
        });
    }
    let hdr = Header::new(op, flags, seq, payload.len() as u32);
    let mut buf = Vec::with_capacity(Header::SIZE + payload.len());
    buf.extend_from_slice(hdr.as_bytes());
    buf.extend_from_slice(payload);
    writer.write_all(&buf).await?;
    Ok(())
}

pub async fn write_ack<W: AsyncWrite + Unpin>(
    writer: &mut W,
    seq: u16,
    status: u8,
) -> Result<()> {
    let payload = AckPayload { status };
    write_frame(writer, Op::Ack, Flags::empty(), seq, payload.as_bytes()).await
}

pub async fn write_pong<W: AsyncWrite + Unpin>(writer: &mut W, seq: u16) -> Result<()> {
    write_frame(writer, Op::Pong, Flags::empty(), seq, &[]).await
}

pub async fn write_err<W: AsyncWrite + Unpin>(
    writer: &mut W,
    seq: u16,
    code: ErrCode,
    msg: &str,
) -> Result<()> {
    let bytes = msg.as_bytes();
    let mlen = u16::try_from(bytes.len()).unwrap_or(u16::MAX);
    let take = mlen as usize;
    let mut payload = Vec::with_capacity(1 + 2 + take);
    payload.push(code as u8);
    payload.extend_from_slice(&mlen.to_le_bytes());
    payload.extend_from_slice(&bytes[..take]);
    write_frame(writer, Op::Err, Flags::empty(), seq, &payload).await
}

/// Build the body of a FRAME (0x90) response.
pub fn build_frame_body(width: u16, height: u16, format: u8, data: &[u8]) -> Vec<u8> {
    let mut body = Vec::with_capacity(FrameHeader::SIZE + data.len());
    let fh = FrameHeader { width, height, format };
    body.extend_from_slice(fh.as_bytes());
    body.extend_from_slice(data);
    body
}
