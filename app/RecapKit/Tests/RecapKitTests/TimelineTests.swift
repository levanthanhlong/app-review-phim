import Foundation
import Testing
@testable import RecapKit

let sampleTimelineJSON = #"""
[
 {"id": "L0001", "type": "narrate", "out_start": 0.0, "out_end": 4.25,
  "segments": [{"src_start": 1.0, "src_end": 5.25, "speed": 1.0}], "tts_file": "tts/vieneu_a.wav", "tts_dur": 4.0, "fit": "ok"},
 {"id": "L0002", "type": "original", "out_start": 4.25, "out_end": 8.25,
  "segments": [{"src_start": 26.0, "src_end": 30.0, "speed": 1.0}], "tts_file": null, "tts_dur": null, "fit": "ok"},
 {"id": "L0003", "type": "narrate", "out_start": 8.25, "out_end": 12.0,
  "segments": [{"src_start": 40.0, "src_end": 43.0, "speed": 0.85}], "tts_file": "tts/vieneu_b.wav", "tts_dur": 3.5, "fit": "short"}
]
"""#

@Suite("Timeline (work/timeline.json)")
struct TimelineTests {
    let entries: [TimelineEntry]

    init() throws {
        entries = try Timeline.decode(Data(sampleTimelineJSON.utf8))
    }

    @Test func decodesEntries() {
        #expect(entries.map(\.id) == ["L0001", "L0002", "L0003"])
        #expect(entries[0].isNarration && !entries[1].isNarration)
        #expect(entries[2].fit == .short && entries[2].segments[0].speed == 0.85)
        #expect(entries[0].ttsFile == "tts/vieneu_a.wav" && entries[1].ttsFile == nil)
        #expect(entries[2].duration == 3.75)
    }

    @Test("finds the line playing at a time", arguments: [
        (0.0, "L0001"), (4.2, "L0001"), (4.25, "L0002"), (8.0, "L0002"), (11.99, "L0003"), (12.0, "L0003"),
    ])
    func lineAtTime(time: Double, expected: String) {
        let index = Timeline.index(at: time, in: entries)
        #expect(index.map { entries[$0].id } == expected)
    }

    @Test("no line before the start or after the end", arguments: [-0.5, 12.5])
    func noLineOutside(time: Double) {
        #expect(Timeline.index(at: time, in: entries) == nil)
    }

    @Test func emptyTimelineHasNoLine() {
        #expect(Timeline.index(at: 1, in: []) == nil)
    }

    @Test func unknownFitIsTreatedAsOK() throws {
        let json = #"[{"id": "L1", "type": "narrate", "out_start": 0, "out_end": 1, "segments": [], "fit": "weird"}]"#
        #expect(try Timeline.decode(Data(json.utf8))[0].fit == .ok)
    }

    @Test func loadReadsTheProjectFile() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir.appending(path: "work"), withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(Timeline.load(from: dir) == nil)

        try Data(sampleTimelineJSON.utf8).write(to: dir.appending(path: "work/timeline.json"))

        #expect(Timeline.load(from: dir)?.count == 3)
    }
}
