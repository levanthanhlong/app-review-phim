import Foundation
import Testing
@testable import RecapKit

@Suite("Beats, beat marks and TTS manifest")
struct SceneBeatsTests {
    let dir: URL

    init() throws {
        dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir.appending(path: "work/tts"), withIntermediateDirectories: true)
    }

    private func write(_ text: String, _ path: String) throws {
        try Data(text.utf8).write(to: dir.appending(path: path))
    }

    @Test func prefersDescribedBeats() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        try write(#"[{"id": "B0001", "start": 0, "end": 10, "shots": [[0, 10]], "keyframe": "keyframes/B0001.jpg"}]"#,
                  "work/scenes.json")
        #expect(SceneBeats.load(from: dir).first?.desc == nil)

        try write(#"""
        [{"id": "B0001", "start": 0, "end": 10, "shots": [[0, 10]], "keyframe": "keyframes/B0001.jpg",
          "dialogue": "Đồng ý", "desc": "Cóc ngồi trong hang"}]
        """#, "work/scenes_described.json")

        let beats = SceneBeats.load(from: dir)
        #expect(beats.map(\.id) == ["B0001"] && beats[0].desc == "Cóc ngồi trong hang" && beats[0].dialogue == "Đồng ý")
        #expect(beats[0].keyframeURL(in: dir).path.hasSuffix("work/keyframes/B0001.jpg"))
        #expect(beats[0].duration == 10)
    }

    @Test func noBeatsYet() {
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(SceneBeats.load(from: dir).isEmpty)
    }

    @Test func marksIgnoreUnknownValues() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(BeatMarks.load(from: dir).isEmpty)

        try write(#"{"B0001": "star", "B0002": "skip", "B0003": "love"}"#, "work/beat_marks.json")

        #expect(BeatMarks.load(from: dir) == ["B0001": .star, "B0002": .skip])
    }

    @Test func keepMarkIsRead() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        try write(#"{"B0001": "keep"}"#, "work/beat_marks.json")
        #expect(BeatMarks.load(from: dir) == ["B0001": .keep])
    }

    @Test func inspectReportGivesAutoSkipsAndCleanup() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(SourceInspection.load(from: dir) == nil)

        try write(#"""
        {"source": {"width": 640, "height": 360, "quality": "low"},
         "watermarks": [{"x": 0.05, "y": 0.8, "w": 0.3, "h": 0.2, "text": "FAPtv"}], "subtitles": [],
         "letterbox": null, "skip_ranges": [{"start": 0, "end": 10.5, "reason": "credit: Đạo diễn"}],
         "skip_beats": {"B0001": "credit: Đạo diễn", "B0002": "quảng cáo"}}
        """#, "work/inspect.json")

        let report = try #require(SourceInspection.load(from: dir))
        #expect(report.skipBeats == ["B0001": "credit: Đạo diễn", "B0002": "quảng cáo"])
        #expect(report.watermarks == ["FAPtv"] && !report.hasLetterbox && report.quality == "low")

        try write(#"{"skip_beats": {"B0003": "credit"}}"#, "work/inspect.json")
        #expect(SourceInspection.load(from: dir)?.skipBeats == ["B0003": "credit"])
    }

    @Test func effectiveSkipFollowsUserMarksOverAutoSkips() {
        let auto = ["B0001": "credit", "B0002": "credit", "B0003": "credit"]
        let marks: [String: BeatMark] = ["B0002": .keep, "B0003": .star, "B0004": .skip]

        let skipped = ["B0001", "B0002", "B0003", "B0004", "B0005"].filter {
            BeatMarks.isSkipped($0, marks: marks, autoSkips: auto)
        }

        #expect(skipped == ["B0001", "B0004"])
    }

    @Test func ttsManifestByLine() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        try write(#"[{"id": "L0001", "file": "tts/vieneu_a.wav", "duration": 3.2, "engine": "vieneu"}]"#,
                  "work/tts/manifest.json")

        let manifest = TTSManifest.load(from: dir)

        #expect(manifest["L0001"]?.duration == 3.2)
        #expect(manifest["L0001"]?.url(in: dir).path.hasSuffix("work/tts/vieneu_a.wav") == true)
    }
}
