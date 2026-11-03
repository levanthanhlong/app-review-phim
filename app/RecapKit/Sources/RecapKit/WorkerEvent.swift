import Foundation

/// One line printed by the worker (`python -m recap ...`), see spec §6.
/// stdout lines are JSON events; stderr lines and anything unparseable become `.raw`.
public enum WorkerEvent: Equatable, Sendable {
    case stageStart(stage: String)
    case progress(stage: String, done: Double, total: Double, message: String)
    case log(level: String, message: String)
    case artifact(stage: String, path: String)
    case stageEnd(stage: String, status: String, elapsedSec: Double, skipped: Bool)
    case error(stage: String?, code: String, message: String, retryable: Bool)
    case result(command: String, data: JSONValue)
    case raw(String)
    /// Synthesized by `WorkerClient` when the process ends (always the last event).
    case exit(code: Int32)

    public static func decode(line: String) -> WorkerEvent {
        guard let data = line.data(using: .utf8),
              let json = try? JSONDecoder().decode(JSONValue.self, from: data),
              let type = json["type"]?.stringValue
        else { return .raw(line) }
        return event(type: type, json: json) ?? .raw(line)
    }

    private static func event(type: String, json: JSONValue) -> WorkerEvent? {
        let stage = json["stage"]?.stringValue
        switch type {
        case "stage_start":
            return stage.map { .stageStart(stage: $0) }
        case "progress":
            guard let stage, let done = json["done"]?.doubleValue, let total = json["total"]?.doubleValue else { return nil }
            return .progress(stage: stage, done: done, total: total, message: json["message"]?.stringValue ?? "")
        case "log":
            return .log(level: json["level"]?.stringValue ?? "info", message: json["message"]?.stringValue ?? "")
        case "artifact":
            guard let stage, let path = json["path"]?.stringValue else { return nil }
            return .artifact(stage: stage, path: path)
        case "stage_end":
            guard let stage, let status = json["status"]?.stringValue else { return nil }
            let skipped = json["skipped"] == .bool(true)
            return .stageEnd(stage: stage, status: status, elapsedSec: json["elapsed_sec"]?.doubleValue ?? 0, skipped: skipped)
        case "error":
            guard let code = json["code"]?.stringValue else { return nil }
            let retryable = json["retryable"] == .bool(true)
            return .error(stage: stage, code: code, message: json["message"]?.stringValue ?? code, retryable: retryable)
        case "result":
            guard let command = json["command"]?.stringValue else { return nil }
            return .result(command: command, data: json["data"] ?? .null)
        default:
            return nil
        }
    }
}
