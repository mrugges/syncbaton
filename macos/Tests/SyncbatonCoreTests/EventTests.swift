import Foundation
import Testing

@testable import SyncbatonCore

func event(
    _ id: Int, _ type: ServerEvent.Kind, user: String? = "alex", note: String? = nil, previous: String? = nil,
    path: String? = nil
)
    -> ServerEvent
{
    ServerEvent(
        id: id, at: Date(), type: type, folder: "music", project: "Song A.logicx", user: user, note: note,
        previousHolder: previous, path: path)
}

@Suite struct EventWatcherTests {
    @Test func yieldsEventsAndAdvancesSince() async throws {
        let server = FakeServer()
        await server.setEvents([
            .success(EventBatch(events: [event(1, .checkout)], lastId: 1)),
            .success(EventBatch(events: [event(2, .checkin)], lastId: 2)),
        ])
        let watcher = EventWatcher(server: server, sleeper: RecordingSleeper())
        var got: [Int] = []
        for await e in watcher.stream() {
            got.append(e.id)
            if got.count == 2 { break }
        }
        #expect(got == [1, 2])
        #expect(Array(await server.eventSinces.prefix(2)) == [nil, 1])
    }

    @Test func reconnectsWithBackoffAfterError() async throws {
        let server = FakeServer()
        await server.setEvents([
            .failure(.transport("down")), .failure(.transport("down")),
            .success(EventBatch(events: [event(5, .checkout)], lastId: 5)),
        ])
        let sleeper = RecordingSleeper()
        let watcher = EventWatcher(server: server, sleeper: sleeper)
        for await _ in watcher.stream() { break }
        let s = await sleeper.sleeps
        #expect(s == [.seconds(1), .seconds(2)])
    }
}

@Suite struct NotificationPlannerTests {
    @Test func ignoresOwnCheckout() {
        #expect(NotificationPlanner.notice(for: event(1, .checkout, user: "sam"), me: "sam") == nil)
    }

    @Test func otherCheckout() {
        let n = NotificationPlanner.notice(for: event(1, .checkout), me: "sam")
        #expect(n?.title == "alex checked out Song A")
    }

    @Test func checkinIncludesNote() {
        let n = NotificationPlanner.notice(for: event(1, .checkin, note: "new bass"), me: "sam")
        #expect(n?.title == "alex handed back Song A")
        #expect(n?.body == "new bass")
    }

    @Test func checkinWithoutNote() {
        let n = NotificationPlanner.notice(for: event(1, .checkin, note: ""), me: "sam")
        #expect(n?.body == "No note")
    }

    @Test func takeOverFromMe() {
        let n = NotificationPlanner.notice(for: event(1, .takeOver, previous: "sam"), me: "sam")
        #expect(n?.title == "alex took over Song A from you")
        #expect(n?.body.contains("without saving") == true)
    }

    @Test func takeOverBetweenOthers() {
        let n = NotificationPlanner.notice(for: event(1, .takeOver, previous: "jo"), me: "sam")
        #expect(n?.title == "alex took over Song A from jo")
    }

    @Test func ownTakeOverIsSilent() {
        #expect(NotificationPlanner.notice(for: event(1, .takeOver, user: "sam", previous: "alex"), me: "sam") == nil)
    }

    @Test func conflict() {
        let n = NotificationPlanner.notice(
            for: event(1, .conflict, user: nil, path: "Song A.logicx/x.sync-conflict-1.wav"), me: "sam")
        #expect(n?.title == "Sync conflict in Song A")
        #expect(n?.body == "Song A.logicx/x.sync-conflict-1.wav")
    }

    @Test func guardWarningNotice() {
        let n = NotificationPlanner.notice(for: Guard.Warning(project: "Song A.logicx", holder: "alex"))
        #expect(n.title == "Song A is checked out by alex")
        #expect(n.body == "Close it without saving.")
    }
}
