import Foundation
import Testing

@testable import SyncbatonCore

@Suite struct GuardTests {
    func project(_ name: String, holder: String?) -> Project {
        Project(name: name, holder: holder, since: nil, heartbeat: nil, stale: false, lastCheckin: nil)
    }

    @Test func warnsWhenOpenAndHeldByOther() {
        let w = Guard.warnings(
            openProjects: ["Song A.logicx"], projects: [project("Song A.logicx", holder: "alex")], me: "sam")
        #expect(w == [Guard.Warning(project: "Song A.logicx", holder: "alex")])
    }

    @Test func noWarningWhenHeldByMe() {
        #expect(
            Guard.warnings(
                openProjects: ["Song A.logicx"], projects: [project("Song A.logicx", holder: "sam")], me: "sam"
            )
            .isEmpty)
    }

    @Test func noWarningWhenFree() {
        #expect(
            Guard.warnings(
                openProjects: ["Song A.logicx"], projects: [project("Song A.logicx", holder: nil)], me: "sam"
            )
            .isEmpty)
    }

    @Test func noWarningWhenNotOpen() {
        #expect(
            Guard.warnings(openProjects: [], projects: [project("Song A.logicx", holder: "alex")], me: "sam").isEmpty)
    }

    @Test func warningsSortedByName() {
        let w = Guard.warnings(
            openProjects: ["B.logicx", "A.logicx"],
            projects: [project("B.logicx", holder: "alex"), project("A.logicx", holder: "alex")], me: "sam")
        #expect(w.map(\.project) == ["A.logicx", "B.logicx"])
    }
}
