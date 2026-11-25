import Foundation

public struct EventWatcher: Sendable {
    private let server: any ServerAPI
    private let sleeper: any Sleeper
    private let wait: Int

    public init(server: any ServerAPI, sleeper: any Sleeper = SystemSleeper(), wait: Int = 30) {
        self.server = server
        self.sleeper = sleeper
        self.wait = wait
    }

    /// Long-polls `/events` until the stream is cancelled. Errors are retried with a backoff of 1 s,
    /// doubling up to 30 s.
    public func stream() -> AsyncStream<ServerEvent> {
        let server = server
        let sleeper = sleeper
        let wait = wait
        return AsyncStream { continuation in
            let task = Task {
                var since: Int?
                var backoff = 1
                while !Task.isCancelled {
                    do {
                        let batch = try await server.events(since: since, wait: wait)
                        since = batch.lastId
                        backoff = 1
                        for event in batch.events { continuation.yield(event) }
                    } catch is CancellationError {
                        break
                    } catch {
                        do { try await sleeper.sleep(for: .seconds(backoff)) } catch { break }
                        backoff = min(backoff * 2, 30)
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
