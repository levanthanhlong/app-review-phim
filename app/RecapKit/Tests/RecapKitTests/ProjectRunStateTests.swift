import Foundation
import Testing
@testable import RecapKit

@MainActor
@Suite("ProjectRunState: what the pipeline view shows while a job runs")
struct ProjectRunStateTests {
    let start = Date(timeIntervalSince1970: 1000)

    @Test func tracksActiveStageProgressAndLogs() {
        let state = ProjectRunState()
        state.begin(jobID: UUID(), title: "Chạy toàn bộ", at: start)

        state.handle(.stageStart(stage: "describe"), at: start)
        state.handle(.progress(stage: "describe", done: 9, total: 45, message: "B0009"), at: start + 90)
        state.handle(.log(level: "warning", message: "chậm"), at: start + 91)

        #expect(state.activeStage == .describe)
        #expect(state.fraction == 0.2)
        #expect(state.progressMessage == "B0009")
        #expect(state.remaining(now: start + 90) == 360)
        #expect(state.logs.last == LogLine(stage: .describe, level: .warning, message: "chậm", date: start + 91))
    }

    @Test func stageEndClearsProgressAndRecordsTheResult() {
        let state = ProjectRunState()
        state.begin(jobID: UUID(), title: "x", at: start)
        state.handle(.stageStart(stage: "tts"), at: start)
        state.handle(.progress(stage: "tts", done: 5, total: 10, message: ""), at: start)

        state.handle(.stageEnd(stage: "tts", status: "done", elapsedSec: 16.2, skipped: false), at: start + 16)

        #expect(state.activeStage == nil && state.fraction == nil)
        #expect(state.logs.last?.message == "Xong: Tạo giọng đọc (16s)")
        #expect(state.finishedStages == [FinishedStage(stage: .tts, elapsedSec: 16.2, skipped: false)])
    }

    @Test func errorsAreKeptForTheBannerUntilTheNextRun() {
        let state = ProjectRunState()
        state.begin(jobID: UUID(), title: "x", at: start)
        state.handle(.stageStart(stage: "script"), at: start)
        state.handle(.error(stage: "script", code: "llm_quota", message: "Hết hạn mức", retryable: true), at: start)
        state.handle(.exit(code: 1), at: start)

        #expect(state.lastError == StageFailureInfo(stage: .script, code: "llm_quota", message: "Hết hạn mức", retryable: true))
        #expect(state.jobID == nil && !state.isActive)
        #expect(state.logs.last?.level == .error)

        state.begin(jobID: UUID(), title: "Thử lại", at: start)
        #expect(state.lastError == nil)
    }

    @Test func cancelledExitIsLoggedAndRawLinesAreMarked() {
        let state = ProjectRunState()
        state.begin(jobID: UUID(), title: "x", at: start)
        state.handle(.raw("Detected language: vi"), at: start)
        state.handle(.exit(code: 130), at: start)

        #expect(state.logs.map(\.level) == [.info, .raw, .warning])
        #expect(state.logs.last?.message == "Đã hủy")
    }

    @Test func logIsCapped() {
        let state = ProjectRunState()
        for n in 0..<(ProjectRunState.maxLogLines + 10) {
            state.handle(.log(level: "info", message: "\(n)"), at: start)
        }
        #expect(state.logs.count == ProjectRunState.maxLogLines)
        #expect(state.logs.last?.message == "\(ProjectRunState.maxLogLines + 9)")
    }

    @Test func decodesDoctorChecks() throws {
        let data = JSONValue.object(["ok": .bool(false), "checks": .array([
            .object(["name": .string("ffmpeg"), "ok": .bool(true), "detail": .string("9.0.2"), "hint": .string(""), "required": .bool(true)]),
            .object(["name": .string("ollama"), "ok": .bool(false), "detail": .string("không chạy"), "hint": .string("ollama serve"), "required": .bool(false)]),
        ])])

        let checks = try DoctorCheck.list(from: data)

        #expect(checks.map(\.name) == ["ffmpeg", "ollama"])
        #expect(checks[1].hint == "ollama serve" && !checks[1].required)
    }
}
