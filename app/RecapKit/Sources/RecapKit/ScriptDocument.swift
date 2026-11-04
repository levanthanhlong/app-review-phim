import Foundation

public struct ScriptClip: Sendable, Codable, Equatable, Hashable {
    public let beat: String
    public let start: Double
    public let end: Double

    public init(beat: String, start: Double, end: Double) {
        self.beat = beat
        self.start = start
        self.end = end
    }
}

/// One line of script.yaml, as `recap script-show` returns it (times in seconds).
/// `narrate` lines have `text` and `clips`; `original` lines have `beat`, `start`, `end` and maybe `note`.
public struct ScriptLine: Sendable, Codable, Equatable, Identifiable {
    public let id: String
    public let type: String
    public let text: String?
    public let clips: [ScriptClip]?
    public let beat: String?
    public let start: Double?
    public let end: Double?
    public let note: String?

    public var isNarration: Bool { type == "narrate" }

    /// Where the line's footage starts in the film.
    public var sourceStart: Double { clips?.first?.start ?? start ?? 0 }

    /// Seconds of footage the script gives the line.
    public var clipSeconds: Double {
        if let clips { return clips.reduce(0) { $0 + $1.end - $1.start } }
        return (end ?? 0) - (start ?? 0)
    }

    public var beatIDs: [String] {
        if let clips { return clips.map(\.beat) }
        return beat.map { [$0] } ?? []
    }

    public static func wordCount(_ text: String) -> Int {
        text.split(whereSeparator: \.isWhitespace).count
    }

    public func withText(_ newText: String) -> ScriptLine {
        ScriptLine(id: id, type: type, text: newText, clips: clips, beat: beat, start: start, end: end, note: note)
    }

    /// The same line shown over the whole of `beat` (picked in the Scene Browser).
    public func withClips(from beat: SceneBeat) -> ScriptLine {
        let clip = ScriptClip(beat: beat.id, start: beat.start, end: beat.end)
        return ScriptLine(id: id, type: type, text: text, clips: [clip], beat: nil, start: nil, end: nil, note: note)
    }
}

/// Length estimates for the editor, mirroring the worker (`script_model.estimate_duration`).
public enum ScriptEstimate {
    public static let defaultWordsPerSec = 3.0
    public static let padSec = 0.25

    /// A narration's real TTS length when known, otherwise words / speed; an original line's footage.
    public static func seconds(_ line: ScriptLine, ttsDuration: Double?, wordsPerSec: Double) -> Double {
        guard line.isNarration else { return line.clipSeconds }
        if let ttsDuration { return ttsDuration + padSec }
        return Double(ScriptLine.wordCount(line.text ?? "")) / wordsPerSec + padSec
    }
}

/// Result of `recap script-show`.
public struct ScriptDocument: Sendable, Codable, Equatable {
    public let title: String
    public let targetDurationSec: Double
    public let wordsPerSec: Double?  // speed the script was written for
    public let voiceWordsPerSec: Double?  // speed of the project's current voice
    public let items: [ScriptLine]

    public init(title: String, targetDurationSec: Double, wordsPerSec: Double?, voiceWordsPerSec: Double?, items: [ScriptLine]) {
        self.title = title
        self.targetDurationSec = targetDurationSec
        self.wordsPerSec = wordsPerSec
        self.voiceWordsPerSec = voiceWordsPerSec
        self.items = items
    }

    /// Speed to estimate narration lengths with: the current voice, else the script's own.
    public var speechRate: Double {
        voiceWordsPerSec ?? wordsPerSec ?? ScriptEstimate.defaultWordsPerSec
    }

    /// The JSON file `recap script-save --file` reads.
    public static func editFile(items: [ScriptLine]) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(["items": items])
    }
}
