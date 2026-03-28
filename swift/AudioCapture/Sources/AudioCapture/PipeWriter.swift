import Foundation

/// Thread-safe writer for a named FIFO that automatically reconnects on broken pipe.
///
/// Both ScreenAudioCapture and MicCapture call `write(_:)` from their respective
/// callback queues.  PipeWriter serialises all writes on a single GCD queue and
/// handles the full FIFO lifecycle:
///
///   1. open(O_WRONLY)  — blocks until a reader connects
///   2. write()         — streams PCM data
///   3. EPIPE           — reader disconnected → close fd, goto 1
///
/// This means the capture callbacks never block and never see FIFO errors.
/// Audio produced while no reader is connected is silently dropped.
final class PipeWriter {
    private let path: String
    private let queue = DispatchQueue(label: "pipe.writer", qos: .userInteractive)
    private var fd: Int32 = -1
    private var connecting = false
    private var stopped = false

    init(path: String) {
        self.path = path
    }

    /// Begin accepting writes. Opens the FIFO in the background (blocks until a reader connects).
    func start() {
        queue.async { self._openFifo() }
    }

    /// Write raw bytes to the FIFO. Safe to call from any thread.
    /// If the FIFO is not connected (no reader), the data is silently dropped.
    func write(_ data: Data) {
        queue.async { [self] in
            guard fd >= 0 else { return } // no reader connected — drop
            let ok = data.withUnsafeBytes { ptr -> Bool in
                guard let base = ptr.baseAddress, ptr.count > 0 else { return true }
                let result = Darwin.write(fd, base, ptr.count)
                return result >= 0
            }
            if !ok {
                // EPIPE or other write error — reader disconnected.
                fputs("PipeWriter: reader disconnected (errno \(errno)), waiting for new reader…\n", stderr)
                Darwin.close(fd)
                fd = -1
                _openFifo() // blocks this queue item until a new reader connects
            }
        }
    }

    /// Stop the writer and close the fd. After this, all writes are dropped.
    func stop() {
        queue.sync {
            stopped = true
            if fd >= 0 {
                Darwin.close(fd)
                fd = -1
            }
        }
    }

    // MARK: - Private

    /// Opens the FIFO for writing. Blocks until a reader opens the read end.
    /// Called on `queue`, so all writes are paused while waiting.
    private func _openFifo() {
        guard !stopped else { return }
        connecting = true
        fputs("PipeWriter: waiting for reader on \(path)…\n", stderr)

        // open(2) with O_WRONLY blocks until a reader opens the other end.
        let newFd = Darwin.open(path, O_WRONLY)
        guard !stopped else {
            if newFd >= 0 { Darwin.close(newFd) }
            return
        }
        if newFd == -1 {
            fputs("PipeWriter: failed to open FIFO (errno \(errno))\n", stderr)
            // Retry after a short delay
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { [self] in
                queue.async { self._openFifo() }
            }
            return
        }
        fd = newFd
        connecting = false
        fputs("PipeWriter: reader connected\n", stderr)
    }
}
