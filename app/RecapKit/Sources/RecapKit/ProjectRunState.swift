import Foundation
import Observation

public enum LogLevel: String, Sendable {
    case info, warning, error
    /// stderr of the worker and its tools (also in logs/<stage>.log); hidden by default.
    case raw
}

public struct LogLine: Sendable, Equatable, Identifiable {
    public let id = UUID()
    public let stage: StageName?
    public let level: LogLevel
    public let message: String
    public let date: Date

    public static func == (lhs: LogLine, rhs: LogLine) -> Bool {
        lhs.stage == rhs.stage && lhs.level == rhs.level && lhs.message == rhs.message && lhs.date == rhs.date
    }
}

public struct StageFailureInfo: Sendable, Equatable {
    public let stage: StageName?
    public let code: String
    public let message: String
    public let retryable: Bool
}

public struct FinishedStage: Sendable, Equatable {
    public let stage: StageName
    public let elapsedSec: Double
    public let skipped: Bool
}

/// Live state of one project while (and after) the worker runs a job for it.
@MainActor
@Observable
public final class ProjectRunState {
    public static let maxLogLines = 2000
    static let cancelledExitCode: Int32 = 130

    public private(set) var jobID: UUID?
    public private(set) var jobTitle = ""
    public private(set) var activeStage: StageName?
    public private(set) var stageStartedAt: Date?
    public private(set) var fraction: Double?
    public private(set) var progressMessage = ""
    public private(set) var logs: [LogLine] = []
    public private(set) var lastError: StageFailureInfo?
    public private(set) var finishedStages: [FinishedStage] = []

    public init() {}

    public var isActive: Bool { jobID != nil }

    public func begin(jobID: UUID, title: String, at date: Date = .now) {
        self.jobID = jobID
        jobTitle = title
        lastError = nil
        finishedStages = []
        append(nil, .info, "▶︎ \(title)", date)
    }

    public func remaining(now: Date = .now) -> Double? {
        guard let fraction, let stageStartedAt else { return nil }
        return Durations.remaining(elapsed: now.timeIntervalSince(stageStartedAt), fraction: fraction)
    }

    public func handle(_ event: WorkerEvent, at date: Date = .now) {
        switch event {
        case let .stageStart(stage):
            activeStage = StageName(rawValue: stage)
            stageStartedAt = date
            fraction = nil
            progressMessage = ""
        case let .progress(_, done, total, message):
            fraction = total > 0 ? min(1, done / total) : nil
            progressMessage = message
        case let .log(level, message):
            append(activeStage, LogLevel(rawValue: level) ?? .info, message, date)
        case let .raw(line):
            append(activeStage, .raw, line, date)
        case let .error(stage, code, message, retryable):
            let failed = stage.flatMap(StageName.init(rawValue:))
            lastError = StageFailureInfo(stage: failed, code: code, message: message, retryable: retryable)
            append(failed, .error, message, date)
        case let .stageEnd(stage, status, elapsedSec, skipped):
            endStage(stage, status: status, elapsedSec: elapsedSec, skipped: skipped, at: date)
        case let .exit(code):
            if code == Self.cancelledExitCode { append(activeStage, .warning, "Đã hủy", date) }
            jobID = nil
            activeStage = nil
            fraction = nil
        case .artifact, .result:
            break
        }
    }

    private func endStage(_ stage: String, status: String, elapsedSec: Double, skipped: Bool, at date: Date) {
        guard let name = StageName(rawValue: stage) else { return }
        activeStage = nil
        fraction = nil
        progressMessage = ""
        guard status == "done" else { return }
        finishedStages.append(FinishedStage(stage: name, elapsedSec: elapsedSec, skipped: skipped))
        let detail = skipped ? "kết quả còn hợp lệ, bỏ qua" : Durations.elapsed(elapsedSec)
        append(name, .info, "Xong: \(name.label) (\(detail))", date)
    }

    private func append(_ stage: StageName?, _ level: LogLevel, _ message: String, _ date: Date) {
        logs.append(LogLine(stage: stage, level: level, message: message, date: date))
        if logs.count > Self.maxLogLines { logs.removeFirst(logs.count - Self.maxLogLines) }
    }
}

/// One line of `recap doctor`.
public struct DoctorCheck: Sendable, Codable, Equatable, Identifiable {
    public let name: String
    public let ok: Bool
    public let detail: String
    public let hint: String
    public let required: Bool

    public var id: String { name }

    public static func list(from result: JSONValue) throws -> [DoctorCheck] {
        try (result["checks"] ?? .array([])).decode([DoctorCheck].self)
    }
}
