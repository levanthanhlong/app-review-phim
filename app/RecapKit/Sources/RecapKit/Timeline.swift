import Foundation

/// How a narration line fitted its footage (worker `fit.py`).
public enum LineFit: String, Sendable, Codable {
    case ok, short, trimmed

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = LineFit(rawValue: raw) ?? .ok
    }
}

public struct TimelineSegment: Sendable, Codable, Equatable {
    public let srcStart: Double
    public let srcEnd: Double
    public let speed: Double
}

/// One line of the recap as placed by `assemble`: where it plays in the recap (`out_*`) and which
/// footage it shows.
public struct TimelineEntry: Sendable, Codable, Equatable, Identifiable {
    public let id: String
    public let type: String  // "narrate" | "original"
    public let outStart: Double
    public let outEnd: Double
    public let segments: [TimelineSegment]
    public let ttsFile: String?
    public let ttsDur: Double?
    public let fit: LineFit

    public var isNarration: Bool { type == "narrate" }
    public var duration: Double { outEnd - outStart }
}

/// Read-only view of `<Project>.recap/work/timeline.json`.
public enum Timeline {
    public static let path = "work/timeline.json"

    public static func decode(_ data: Data) throws -> [TimelineEntry] {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode([TimelineEntry].self, from: data)
    }

    /// nil until `assemble` has run (or while the file is being rewritten).
    public static func load(from projectDir: URL) -> [TimelineEntry]? {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)) else { return nil }
        return try? decode(data)
    }

    /// Index of the line playing at `time` (seconds into the recap); the very end belongs to the last line.
    public static func index(at time: Double, in entries: [TimelineEntry]) -> Int? {
        guard let last = entries.last, time >= 0, time <= last.outEnd else { return nil }
        var low = 0
        var high = entries.count - 1
        while low < high {  // last entry whose start is <= time
            let mid = (low + high + 1) / 2
            if entries[mid].outStart <= time { low = mid } else { high = mid - 1 }
        }
        return low
    }
}
