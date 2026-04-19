import XCTest
import Network
@testable import AudioCaptureCore

final class TcpStreamWriterTests: XCTestCase {

    /// Spins up an NWListener on an ephemeral port and exposes the first
    /// connection's received bytes through an async collector.
    final class LocalListener {
        let listener: NWListener
        var port: UInt16 { listener.port?.rawValue ?? 0 }
        private var received = Data()
        private let lock = NSLock()
        private let receivedExpectation: XCTestExpectation?

        init(expectedBytes: Int, waiter: XCTestExpectation?) throws {
            let params = NWParameters.tcp
            self.listener = try NWListener(using: params, on: .any)
            self.receivedExpectation = waiter
            let expected = expectedBytes
            listener.newConnectionHandler = { conn in
                conn.start(queue: .main)
                func receive() {
                    conn.receive(minimumIncompleteLength: 1, maximumLength: 4096) {
                        [weak self] data, _, isComplete, err in
                        guard let self = self else { return }
                        if let data = data, !data.isEmpty {
                            self.lock.lock()
                            self.received.append(data)
                            let done = self.received.count >= expected
                            self.lock.unlock()
                            if done { self.receivedExpectation?.fulfill() }
                        }
                        if isComplete || err != nil { return }
                        receive()
                    }
                }
                receive()
            }
        }

        func start() {
            listener.start(queue: .main)
        }

        func snapshot() -> Data {
            lock.lock(); defer { lock.unlock() }
            return received
        }

        func stop() {
            listener.cancel()
        }
    }

    func testHandshakeBytesAreSentFirst() throws {
        let exp = expectation(description: "received handshake")
        let srv = try LocalListener(expectedBytes: 2, waiter: exp)
        srv.start()
        // Wait briefly for the listener to publish a port
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))

        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: srv.port,
            streamTag: 0x01,
        )
        writer.start()
        // No payload write — handshake alone should arrive
        wait(for: [exp], timeout: 2.0)

        let bytes = srv.snapshot()
        XCTAssertEqual(bytes[0], 0xAD)
        XCTAssertEqual(bytes[1], 0x01)

        writer.stop()
        srv.stop()
    }

    func testPayloadBytesFollowHandshake() throws {
        let exp = expectation(description: "received payload")
        let payload = Data([0x10, 0x20, 0x30, 0x40])
        let srv = try LocalListener(expectedBytes: 2 + payload.count, waiter: exp)
        srv.start()
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))

        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: srv.port,
            streamTag: 0x02,
        )
        writer.start()
        // Give the connection a moment to complete before writing
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        writer.write(payload)

        wait(for: [exp], timeout: 2.0)

        let bytes = srv.snapshot()
        XCTAssertEqual(bytes[0], 0xAD)
        XCTAssertEqual(bytes[1], 0x02)
        XCTAssertEqual(bytes.subdata(in: 2..<bytes.count), payload)

        writer.stop()
        srv.stop()
    }

    func testWriteBeforeConnectIsDropped() throws {
        // No listener → connect fails → writes are no-ops, not crashes.
        let writer = TcpStreamWriter(
            host: "127.0.0.1",
            port: 1, // reserved, will refuse
            streamTag: 0x01,
        )
        writer.start()
        writer.write(Data([0x00, 0x00]))
        // Nothing to assert — test passes if no crash within 0.2s
        RunLoop.main.run(until: Date().addingTimeInterval(0.2))
        writer.stop()
    }
}
