import Foundation

/// One generated track of an AI music cue (worker `music_options`).
public struct MusicOption: Sendable, Codable, Equatable, Identifiable {
    public let label: String
    public let prompt: String
    public let seed: Int
    public let seconds: Double
    public let engine: String
    public let model: String
    public let file: String  // relative to work/

    public var id: String { label }

    public func url(in projectDir: URL) -> URL {
        projectDir.appending(path: "work").appending(path: file)
    }
}

/// A stretch of the recap with one mood and its three music options.
public struct MusicPlanCue: Sendable, Codable, Equatable, Identifiable {
    public let id: String
    public let firstLine: String
    public let lastLine: String
    public let outStart: Double
    public let outEnd: Double
    public let mood: String
    public let prompt: String
    public let altPrompt: String
    public let chosen: Int
    public let options: [MusicOption]

    public var chosenOption: MusicOption? {
        options.indices.contains(chosen) ? options[chosen] : nil
    }
}

/// Read-only view of `<Project>.recap/work/music/plan.json`; changes go through `recap music-choose/-edit`.
public struct MusicPlan: Sendable, Codable, Equatable {
    public let model: String?
    public let cues: [MusicPlanCue]

    public static func decode(_ data: Data) throws -> MusicPlan {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(MusicPlan.self, from: data)
    }

    /// nil when the music stage has not planned anything yet (or the file is being rewritten).
    public static func load(from projectDir: URL) -> MusicPlan? {
        guard let data = try? Data(contentsOf: projectDir.appending(path: "work/music/plan.json")) else { return nil }
        return try? decode(data)
    }
}
