import Testing

@testable import SyncbatonCore

@Suite struct TitleParserTests {
    let known = ["Song A.logicx", "Song B.logicx"]

    @Test func stripsViewSuffix() {
        #expect(LogicTitleParser.match(title: "Song A - Tracks", known: known) == "Song A.logicx")
    }

    @Test func handlesEnAndEmDashSeparators() {
        #expect(LogicTitleParser.match(title: "Song A \u{2013} Mixer", known: known) == "Song A.logicx")
        #expect(LogicTitleParser.match(title: "Song A \u{2014} Piano Roll", known: known) == "Song A.logicx")
    }

    @Test func titleWithoutSuffix() {
        #expect(LogicTitleParser.match(title: "Song B", known: known) == "Song B.logicx")
    }

    @Test func keepsExistingExtension() {
        #expect(LogicTitleParser.match(title: "Song A.logicx - Tracks", known: known) == "Song A.logicx")
    }

    @Test func editedMarker() {
        #expect(LogicTitleParser.match(title: "Song A - Edited", known: known) == "Song A.logicx")
    }

    @Test func longestMatchWinsWhenOneNameIsPrefixOfAnother() {
        let k = ["Mix.logicx", "Mix - Remix.logicx"]
        #expect(LogicTitleParser.match(title: "Mix - Remix - Tracks", known: k) == "Mix - Remix.logicx")
        #expect(LogicTitleParser.match(title: "Mix - Tracks", known: k) == "Mix.logicx")
    }

    @Test func similarNamesDoNotMatch() {
        #expect(LogicTitleParser.match(title: "Song AB - Tracks", known: known) == nil)
    }

    @Test func unknownAndBlankTitles() {
        #expect(LogicTitleParser.match(title: "Other - Tracks", known: known) == nil)
        #expect(LogicTitleParser.match(title: "  ", known: known) == nil)
    }

    @Test func projectNamesUnionsAllWindows() {
        let names = LogicTitleParser.projectNames(
            fromTitles: ["Song A - Tracks", "Song A - Mixer", "Song B - Tracks", "Logic Pro"], known: known)
        #expect(names == ["Song A.logicx", "Song B.logicx"])
    }
}

@Suite struct AdapterRegistryTests {
    @Test func logicProjectsGetDetection() {
        #expect(AdapterRegistry.adapter(forProject: "Song A.logicx").supportsDetection)
        #expect(AdapterRegistry.adapter(forProject: "Song A.logicx").needsAccessibility)
    }

    @Test func otherProjectsDoNot() {
        #expect(!AdapterRegistry.adapter(forProject: "Song.als").supportsDetection)
        #expect(!AdapterRegistry.adapter(forProject: "Song.als").needsAccessibility)
    }
}
