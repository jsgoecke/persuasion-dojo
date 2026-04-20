import Foundation
import Darwin

/// Thread-safe writer that streams raw bytes to a TCP server after sending
/// a 2-byte handshake (0xAD magic + stream tag).
///
/// Public API matches the removed PipeWriter:
///   - start()              kicks off connection establishment
///   - write(_ data: Data)  enqueues bytes; safe from any thread
///   - stop()               closes the socket and drops pending writes
///
/// If the server is unreachable or the connection drops, the writer
/// reconnects with a 500 ms backoff. Writes issued while disconnected
/// are silently dropped (audio is realtime — buffering old bytes is
/// worse than dropping them).
public final class TcpStreamWriter {
    private static let handshakeMagic: UInt8 = 0xAD
    private static let reconnectDelayNs: UInt64 = 500_000_000 // 500 ms

    private let host: String
    private let port: UInt16
    private let streamTag: UInt8
    private let queue = DispatchQueue(label: "tcp.stream.writer", qos: .userInteractive)

    // `fd` is only touched on `queue`.
    private var fd: Int32 = -1

    // `stopped` is read from `queue` AND written from stop() (any thread),
    // so it must be protected independently of the queue — otherwise
    // stop() deadlocks against connectLoop's retry sleep.
    private let stopLock = NSLock()
    private var _stopped = false
    private var stopped: Bool {
        stopLock.lock(); defer { stopLock.unlock() }
        return _stopped
    }

    public init(host: String, port: UInt16, streamTag: UInt8) {
        self.host = host
        self.port = port
        self.streamTag = streamTag
    }

    public func start() {
        queue.async { [self] in self.connectLoop() }
    }

    public func write(_ data: Data) {
        queue.async { [self] in
            guard fd >= 0 else { return }
            let ok = data.withUnsafeBytes { ptr -> Bool in
                guard let base = ptr.baseAddress, ptr.count > 0 else { return true }
                // Loop until all bytes are written or an error occurs —
                // stream sockets can legally return a short count.
                var sent = 0
                let total = ptr.count
                while sent < total {
                    let n = Darwin.write(fd, base.advanced(by: sent), total - sent)
                    if n <= 0 { return false }
                    sent += n
                }
                return true
            }
            if !ok {
                fputs("TcpStreamWriter: write failed (errno \(errno)), reconnecting…\n",
                      stderr)
                Darwin.close(fd)
                fd = -1
                connectLoop()
            }
        }
    }

    public func stop() {
        // Flip the stop flag without going through `queue` — connectLoop
        // may be holding the queue inside a backoff sleep, so a
        // queue.sync here would deadlock.
        stopLock.lock()
        _stopped = true
        stopLock.unlock()

        // Close the socket asynchronously on the queue (serialized w.r.t. writes).
        queue.async { [self] in
            if fd >= 0 {
                Darwin.close(fd)
                fd = -1
            }
        }
    }

    // MARK: - Private

    private func connectLoop() {
        while !stopped {
            if connectOnce() { return }
            // Backoff before retry. Sleep in small slices so stop() is
            // honored promptly (avoids up-to-500ms tail latency on shutdown).
            for _ in 0..<10 {
                if stopped { return }
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
    }

    /// Returns true on success; false means caller should back off and retry.
    private func connectOnce() -> Bool {
        let sock = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return false }

        // Prevent SIGPIPE killing the process if the peer closes mid-write.
        // Best-effort; ignoring the return value is intentional.
        var one: Int32 = 1
        setsockopt(sock, SOL_SOCKET, SO_NOSIGPIPE, &one,
                   socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        // inet_pton returns 1 on success, 0 on malformed literal, -1 on EAFNOSUPPORT.
        // Anything other than 1 means we'd silently bind to 0.0.0.0 — fail fast.
        if inet_pton(AF_INET, host, &addr.sin_addr) != 1 {
            Darwin.close(sock)
            fputs("TcpStreamWriter: invalid IPv4 host \(host)\n", stderr)
            return false
        }

        let connectRes = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if connectRes != 0 {
            Darwin.close(sock)
            return false
        }

        let handshake: [UInt8] = [Self.handshakeMagic, streamTag]
        let sent = handshake.withUnsafeBufferPointer { buf -> Int in
            Darwin.write(sock, buf.baseAddress, buf.count)
        }
        if sent != handshake.count {
            Darwin.close(sock)
            return false
        }
        fd = sock
        fputs("TcpStreamWriter: connected (tag \(streamTag))\n", stderr)
        return true
    }
}
