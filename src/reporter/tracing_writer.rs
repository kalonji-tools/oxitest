//! Routes tracing output through indicatif's `pb.println()` when a progress
//! bar is active. Falls back to stderr when no progress bar is registered.

use std::io::{self, Write};
use std::sync::{Arc, OnceLock};

use indicatif::ProgressBar;
use parking_lot::Mutex;

type PbHandle = Arc<Mutex<Option<ProgressBar>>>;

static HANDLE: OnceLock<PbHandle> = OnceLock::new();

fn shared_handle() -> PbHandle {
    Arc::clone(HANDLE.get_or_init(|| Arc::new(Mutex::new(None))))
}

/// Register a progress bar so tracing output routes through `pb.println()`.
pub(crate) fn register(pb: ProgressBar) {
    *shared_handle().lock() = Some(pb);
}

/// Deregister the progress bar so tracing output falls back to stderr.
pub(crate) fn deregister() {
    *shared_handle().lock() = None;
}

// ─── MakeWriter ──────────────────────────────────────────────────────────────

/// Factory that produces [`PbWriter`] instances for `tracing_subscriber`.
pub(crate) struct PbMakeWriter {
    handle: PbHandle,
}

impl PbMakeWriter {
    pub(crate) fn new() -> Self {
        Self {
            handle: shared_handle(),
        }
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for PbMakeWriter {
    type Writer = PbWriter;

    fn make_writer(&'a self) -> Self::Writer {
        PbWriter {
            handle: Arc::clone(&self.handle),
            buf: Vec::new(),
        }
    }
}

// ─── Writer ──────────────────────────────────────────────────────────────────

/// Buffers a single tracing event, then flushes it through `pb.println()` on
/// drop (or explicit `flush()`). Falls back to `eprintln!` when no progress
/// bar is registered.
pub(crate) struct PbWriter {
    handle: PbHandle,
    buf: Vec<u8>,
}

impl io::Write for PbWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buf.extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.buf.is_empty() {
            return Ok(());
        }
        let msg = String::from_utf8_lossy(&self.buf);
        let msg = msg.trim_end();
        if !msg.is_empty() {
            let guard = self.handle.lock();
            if let Some(pb) = &*guard {
                pb.println(msg);
            } else {
                eprintln!("{msg}");
            }
        }
        self.buf.clear();
        Ok(())
    }
}

impl Drop for PbWriter {
    fn drop(&mut self) {
        let _ = self.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_writer_buffers_and_flushes_without_panic() {
        let handle: PbHandle = Arc::new(Mutex::new(None));
        let mut writer = PbWriter {
            handle,
            buf: Vec::new(),
        };
        writer.write_all(b"hello world\n").unwrap();
        writer.flush().unwrap();
        assert!(writer.buf.is_empty(), "buffer must be cleared after flush");
    }

    #[test]
    fn test_writer_routes_through_progress_bar_when_registered() {
        let pb = ProgressBar::hidden();
        let handle: PbHandle = Arc::new(Mutex::new(Some(pb)));
        let mut writer = PbWriter {
            handle,
            buf: Vec::new(),
        };
        writer.write_all(b"routed line\n").unwrap();
        writer.flush().unwrap();
        assert!(writer.buf.is_empty());
    }

    #[test]
    fn test_writer_drop_flushes_remaining_buffer() {
        let handle: PbHandle = Arc::new(Mutex::new(None));
        {
            let mut writer = PbWriter {
                handle: Arc::clone(&handle),
                buf: Vec::new(),
            };
            writer.write_all(b"drop me\n").unwrap();
        }
        // No panic means Drop flushed successfully.
    }

    #[test]
    fn test_writer_empty_buffer_flush_is_noop() {
        let handle: PbHandle = Arc::new(Mutex::new(None));
        let mut writer = PbWriter {
            handle,
            buf: Vec::new(),
        };
        writer.flush().unwrap();
        assert!(writer.buf.is_empty());
    }

    #[test]
    fn test_make_writer_produces_independent_writers() {
        let mw = PbMakeWriter {
            handle: Arc::new(Mutex::new(None)),
        };
        use tracing_subscriber::fmt::MakeWriter;
        let mut w1 = mw.make_writer();
        let mut w2 = mw.make_writer();
        w1.write_all(b"one").unwrap();
        w2.write_all(b"two").unwrap();
        assert_ne!(w1.buf, w2.buf, "writers must have independent buffers");
    }

    #[test]
    fn test_register_and_deregister_lifecycle() {
        register(ProgressBar::hidden());
        assert!(shared_handle().lock().is_some());
        deregister();
        assert!(shared_handle().lock().is_none());
    }
}
