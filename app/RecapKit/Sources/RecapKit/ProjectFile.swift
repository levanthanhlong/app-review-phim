import Foundation

/// The pipeline stages, in order (worker `STAGE_NAMES`).
public enum StageName: String, CaseIterable, Sendable, Codable, Identifiable {
    case ingest, transcribe, scenes, describe, script, tts, music, assemble, export

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .ingest: "Nạp phim"
        case .transcribe: "Nhận dạng lời thoại"
        case .scenes: "Chia cảnh"
        case .describe: "Mô tả cảnh"
        case .script: "Viết kịch bản"
        case .tts: "Tạo giọng đọc"
        case .music: "Tạo nhạc nền"
        case .assemble: "Ghép bản xem trước"
        case .export: "Xuất bản cuối"
        }
    }
}

public enum StageStatus: String, Sendable, Codable {
    case pending, running, done, failed, stale
    /// App-only: `running` in project.json but no worker process is running it (app quit, crash).
    case interrupted
}

public struct StageErrorInfo: Sendable, Codable, Equatable {
    public let code: String
    public let message: String
    public let retryable: Bool
}

public struct StageState: Sendable, Codable, Equatable {
    public let status: StageStatus
    public let progress: Double?
    public let startedAt: String?
    public let finishedAt: String?
    public let elapsedSec: Double?
    public let error: StageErrorInfo?

    public static let pending = StageState(status: .pending, progress: 0, startedAt: nil, finishedAt: nil, elapsedSec: nil, error: nil)
}

/// The settings the app shows. Unknown keys are ignored; the worker owns the full schema
/// (the app changes settings only through `recap set`).
public struct ProjectSettings: Sendable, Codable, Equatable {
    public let targetRatio: Double?
    public let narrationStyle: String?
    public let ttsEngine: String?
    public let voice: String?
    public let ttsRate: String?
    public let duckVolume: Double?
    public let musicFile: String?
    public let musicVolume: Double?
    public let musicBedVolume: Double?
    public let musicSource: String?  // "library" | "ai"
    public let musicModel: String?  // Stable Audio 3 DiT: "medium" | "sm-music"
    public let brandBanner: String?  // channel banner below the film (nil = no branding)
    public let brandLogo: String?
    public let subtitleFont: String?
}

public struct MediaInfo: Sendable, Codable, Equatable {
    public let duration: Double
    public let width: Int?
    public let height: Int?
    public let fps: Double?
}

/// Read-only view of `<Project>.recap/project.json`.
public struct ProjectFile: Sendable, Codable, Equatable {
    public let id: String
    public let title: String
    public let sourcePath: String
    public let createdAt: String
    public let settings: ProjectSettings
    public let media: MediaInfo?
    public let stages: [String: StageState]

    public static func decode(_ data: Data) throws -> ProjectFile {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ProjectFile.self, from: data)
    }

    public static func load(from projectDir: URL) throws -> ProjectFile {
        try decode(Data(contentsOf: projectDir.appending(path: "project.json")))
    }

    public func state(of stage: StageName) -> StageState {
        stages[stage.rawValue] ?? .pending
    }

    public var orderedStages: [(name: StageName, state: StageState)] {
        StageName.allCases.map { ($0, state(of: $0)) }
    }

    public var targetDuration: Double? {
        guard let duration = media?.duration, let ratio = settings.targetRatio else { return nil }
        return duration * ratio
    }

    /// Status to show for one stage; `isRunning` = a worker process is working on this project now.
    public func displayStatus(of stage: StageName, isRunning: Bool) -> StageStatus {
        let status = state(of: stage).status
        return status == .running && !isRunning ? .interrupted : status
    }

    /// One status for the project list.
    public func overallStatus(isRunning: Bool) -> StageStatus {
        let all = StageName.allCases.map { displayStatus(of: $0, isRunning: isRunning) }
        for status in [StageStatus.running, .failed, .interrupted, .stale] where all.contains(status) {
            return status
        }
        return all.allSatisfy { $0 == .done } ? .done : .pending
    }
}
