import Foundation
import Testing
@testable import RecapKit

/// What `recap script-show` returns (times in seconds).
let sampleScriptShowJSON = #"""
{
 "title": "Cóc Kiện Trời", "target_duration_sec": 8, "words_per_sec": 3.0, "voice_words_per_sec": 4.0,
 "items": [
  {"id": "L0001", "type": "narrate", "text": "Ngày xưa có một chú cóc nhỏ sống trong hang tối.",
   "clips": [{"beat": "B0001", "start": 1.0, "end": 5.0}]},
  {"id": "L0002", "type": "original", "beat": "B0003", "start": 26.0, "end": 30.0, "note": "gật đầu"}
 ]
}
"""#

@Suite("Script document (recap script-show / script-save)")
struct ScriptDocumentTests {
    let document: ScriptDocument

    init() throws {
        document = try JSONDecoder().decode(JSONValue.self, from: Data(sampleScriptShowJSON.utf8)).decode(ScriptDocument.self)
    }

    @Test func decodesWorkerResult() {
        #expect(document.title == "Cóc Kiện Trời" && document.targetDurationSec == 8)
        #expect(document.items.map(\.id) == ["L0001", "L0002"])
        let narrate = document.items[0]
        #expect(narrate.isNarration && narrate.clips?.first == ScriptClip(beat: "B0001", start: 1, end: 5))
        let original = document.items[1]
        #expect(!original.isNarration && original.beat == "B0003" && original.note == "gật đầu")
    }

    @Test func sourceRangeAndBeatsOfALine() {
        #expect(document.items[0].sourceStart == 1 && document.items[0].clipSeconds == 4)
        #expect(document.items[1].sourceStart == 26 && document.items[1].clipSeconds == 4)
        #expect(document.items[0].beatIDs == ["B0001"] && document.items[1].beatIDs == ["B0003"])
    }

    @Test("counts words like the worker (split on whitespace)", arguments: [
        ("Một hai  ba\nbốn", 4), ("", 0), ("   ", 0),
    ])
    func wordCount(text: String, expected: Int) {
        #expect(ScriptLine.wordCount(text) == expected)
    }

    @Test func estimatesNarrationFromVoiceSpeedOrTTS() {
        let line = document.items[0]  // 11 words
        #expect(ScriptEstimate.seconds(line, ttsDuration: nil, wordsPerSec: 4) == 11.0 / 4 + 0.25)
        #expect(ScriptEstimate.seconds(line, ttsDuration: 3.1, wordsPerSec: 4) == 3.1 + 0.25)
        #expect(ScriptEstimate.seconds(document.items[1], ttsDuration: nil, wordsPerSec: 4) == 4)
    }

    @Test func speedPrefersTheCurrentVoice() {
        #expect(document.speechRate == 4.0)
        let old = ScriptDocument(title: "", targetDurationSec: 0, wordsPerSec: 2.5, voiceWordsPerSec: nil, items: [])
        #expect(old.speechRate == 2.5)
        let none = ScriptDocument(title: "", targetDurationSec: 0, wordsPerSec: nil, voiceWordsPerSec: nil, items: [])
        #expect(none.speechRate == ScriptEstimate.defaultWordsPerSec)
    }

    @Test func editFileHasOnlyTheItems() throws {
        let edited = document.items[0].withText("Câu mới.")
        let data = try ScriptDocument.editFile(items: [edited, document.items[1]])

        let json = try JSONDecoder().decode(JSONValue.self, from: data)
        let first = try json["items"]?.decode([ScriptLine].self).first
        #expect(first?.text == "Câu mới." && first?.clips?.count == 1)
        #expect(json["title"] == nil)
        // nil fields are left out, not written as null (the worker would reject `note: null` on a narrate line)
        #expect(!String(decoding: data, as: UTF8.self).contains("null"))
    }

    @Test func replacingClipsUsesTheWholeBeat() {
        let beat = SceneBeat(id: "B0002", start: 10, end: 25, keyframe: "keyframes/B0002.jpg", dialogue: "", desc: nil)

        let changed = document.items[0].withClips(from: beat)

        #expect(changed.clips == [ScriptClip(beat: "B0002", start: 10, end: 25)])
        #expect(changed.text == document.items[0].text && changed.id == "L0001")
    }
}
