import Foundation

/// One beat of `work/scenes_described.json` (or `scenes.json` before `describe` has run).
public struct SceneBeat: Sendable, Codable, Equatable, Identifiable {
    public let id: String
    public let start: Double
    public let end: Double
    public let keyframe: String  // relative to work/
    public let dialogue: String
    public let desc: String?

    public init(id: String, start: Double, end: Double, keyframe: String, dialogue: String, desc: String?) {
        self.id = id
        self.start = start
        self.end = end
        self.keyframe = keyframe
        self.dialogue = dialogue
        self.desc = desc
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        start = try c.decode(Double.self, forKey: .start)
        end = try c.decode(Double.self, forKey: .end)
        keyframe = try c.decode(String.self, forKey: .keyframe)
        dialogue = try c.decodeIfPresent(String.self, forKey: .dialogue) ?? ""
        desc = try c.decodeIfPresent(String.self, forKey: .desc)
    }

    public var duration: Double { end - start }

    public func keyframeURL(in projectDir: URL) -> URL {
        projectDir.appending(path: "work").appending(path: keyframe)
    }
}

public enum SceneBeats {
    static let described = "work/scenes_described.json"
    static let scenes = "work/scenes.json"

    /// The described beats when available, else the plain ones; empty before `scenes` has run.
    public static func load(from projectDir: URL) -> [SceneBeat] {
        for path in [described, scenes] {
            if let data = try? Data(contentsOf: projectDir.appending(path: path)),
               let beats = try? JSONDecoder().decode([SceneBeat].self, from: data) {
                return beats
            }
        }
        return []
    }
}

/// A beat the user marked in the Scene Browser (worker `beat_marks.py`).
/// `keep`: used although the `inspect` stage wanted it skipped.
public enum BeatMark: String, Sendable, Codable, CaseIterable {
    case star, skip, keep
}

public enum BeatMarks {
    static let path = "work/beat_marks.json"

    /// Read-only: marks change through `recap beat-mark`. Unknown values are ignored.
    public static func load(from projectDir: URL) -> [String: BeatMark] {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)),
              let raw = try? JSONDecoder().decode([String: String].self, from: data)
        else { return [:] }
        return raw.compactMapValues(BeatMark.init(rawValue:))
    }

    /// Whether the script leaves the beat out: marked skip, or flagged by `inspect` and not marked by the user
    /// (same rule as the worker's `effective_marks`).
    public static func isSkipped(_ beat: String, marks: [String: BeatMark], autoSkips: [String: String]) -> Bool {
        if let mark = marks[beat] { return mark == .skip }
        return autoSkips[beat] != nil
    }
}

/// What the `inspect` stage found in the source (`work/inspect.json`), as far as the app shows it.
public struct SourceInspection: Sendable, Equatable {
    public let skipBeats: [String: String]  // beat id -> reason
    public let watermarks: [String]  // their text ("" = logo without text)
    public let hasLetterbox: Bool
    public let quality: String  // ok | acceptable | low

    static let path = "work/inspect.json"

    private struct Report: Decodable {
        struct Source: Decodable { let quality: String }
        struct Mark: Decodable { let text: String? }
        struct Box: Decodable { let w: Double }
        // Optional: a field missing from a newer or older report hides only that part.
        let source: Source?
        let watermarks: [Mark]?
        let letterbox: Box?
        let skip_beats: [String: String]?
    }

    /// nil when `inspect` has not run (or the report cannot be read).
    public static func load(from projectDir: URL) -> SourceInspection? {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)),
              let report = try? JSONDecoder().decode(Report.self, from: data)
        else { return nil }
        return SourceInspection(skipBeats: report.skip_beats ?? [:], watermarks: (report.watermarks ?? []).map { $0.text ?? "" },
                                hasLetterbox: report.letterbox != nil, quality: report.source?.quality ?? "ok")
    }
}

/// One narration wav of `work/tts/manifest.json`.
public struct TTSLine: Sendable, Codable, Equatable {
    public let id: String
    public let file: String  // relative to work/
    public let duration: Double

    public func url(in projectDir: URL) -> URL {
        projectDir.appending(path: "work").appending(path: file)
    }
}

public enum TTSManifest {
    static let path = "work/tts/manifest.json"

    /// Line id -> its narration wav; empty before `tts` has run.
    public static func load(from projectDir: URL) -> [String: TTSLine] {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)),
              let lines = try? JSONDecoder().decode([TTSLine].self, from: data)
        else { return [:] }
        return Dictionary(lines.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    }
}
