import CryptoKit
import Foundation

/// One `<Title>.recap` folder in the projects root.
public struct ProjectSummary: Identifiable, Sendable, Equatable {
    public let id: URL  // the project folder
    public let file: ProjectFile
    public let thumbnail: URL?
}

public enum ProjectLibrary {
    static let suffix = "recap"

    /// Every readable project under `root`, newest first. Folders without a valid project.json are skipped.
    public static func scan(root: URL) -> [ProjectSummary] {
        let folders = (try? FileManager.default.contentsOfDirectory(
            at: root, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]
        )) ?? []
        return folders
            .filter { $0.pathExtension == suffix }
            .compactMap(summary)
            .sorted { $0.file.createdAt > $1.file.createdAt }
    }

    public static func summary(of dir: URL) -> ProjectSummary? {
        guard let file = try? ProjectFile.load(from: dir) else { return nil }
        let keyframe = dir.appending(path: "work/keyframes/B0001.jpg")
        let thumbnail = FileManager.default.fileExists(atPath: keyframe.path) ? keyframe : nil
        return ProjectSummary(id: dir.standardizedFileURL, file: file, thumbnail: thumbnail)
    }
}

/// A narration voice from `recap voices`.
public struct Voice: Sendable, Codable, Equatable, Identifiable, Hashable {
    public let engine: String
    public let id: String
    public let description: String
    public let gender: String
    public let region: String
    public let featured: Bool
    public let wordsPerSec: Double

    public init(engine: String, id: String, description: String, gender: String, region: String, featured: Bool, wordsPerSec: Double) {
        self.engine = engine
        self.id = id
        self.description = description
        self.gender = gender
        self.region = region
        self.featured = featured
        self.wordsPerSec = wordsPerSec
    }

    public var label: String { id }
    public var isMale: Bool { gender == "male" }
}

public enum VoiceCatalog {
    public static func voices(from result: JSONValue) throws -> [Voice] {
        try (result["voices"] ?? .array([])).decode([Voice].self)
    }
}

/// Voice previews are cached by (engine, voice, rate, text) so each sample is made once.
public struct VoicePreviewCache: Sendable {
    public let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    public func url(engine: String, voice: String, rate: String, text: String) -> URL {
        let key = [engine, voice, rate, text].joined(separator: "\u{1F}")
        let digest = SHA256.hash(data: Data(key.utf8)).prefix(12).map { String(format: "%02x", $0) }.joined()
        return directory.appending(path: "\(digest).wav")
    }

    public func cached(engine: String, voice: String, rate: String, text: String) -> URL? {
        let file = url(engine: engine, voice: voice, rate: rate, text: text)
        return FileManager.default.fileExists(atPath: file.path) ? file : nil
    }
}

public enum Durations {
    static let minFractionForEstimate = 0.02

    /// "7:27", "1:02:05".
    public static func clock(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        let (hours, minutes, secs) = (total / 3600, total % 3600 / 60, total % 60)
        return hours > 0 ? String(format: "%d:%02d:%02d", hours, minutes, secs) : String(format: "%d:%02d", minutes, secs)
    }

    /// Seconds left, assuming the rest goes as fast as the part already done; nil when too early to tell.
    public static func remaining(elapsed: Double, fraction: Double) -> Double? {
        guard fraction > minFractionForEstimate else { return nil }
        guard fraction < 1 else { return 0 }
        return elapsed / fraction * (1 - fraction)
    }

    public static func rateText(percent: Int) -> String {
        percent < 0 ? "\(percent)%" : "+\(percent)%"
    }

    public static func ratePercent(_ text: String) -> Int {
        Int(text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "%", with: "")) ?? 0
    }
}

extension Durations {
    /// "16s", "7 phút 18s".
    public static func elapsed(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return total < 60 ? "\(total)s" : "\(total / 60) phút \(total % 60)s"
    }
}
