import Foundation
import Testing
@testable import RecapKit

/// The app's calls against the real worker (python + ffmpeg). Opt-in: RECAP_INTEGRATION=1 swift test
enum RealWorker {
    static let repo = URL(filePath: #filePath).deletingLastPathComponent()  // .../Tests/RecapKitTests
        .deletingLastPathComponent().deletingLastPathComponent()  // .../app/RecapKit
        .deletingLastPathComponent().deletingLastPathComponent()  // repo root
    static let enabled = ProcessInfo.processInfo.environment["RECAP_INTEGRATION"] == "1"
    static let client = WorkerClient(config: .python(workerDirectory: repo.appending(path: "worker"),
                                                     extraPath: ["/opt/homebrew/bin", "/usr/local/bin"]))
    static let sample = repo.appending(path: "samples/clip.mp4")
}

@Suite("App ↔ real worker", .enabled(if: RealWorker.enabled), .serialized)
struct RealWorkerTests {
    @Test func voicesListTheVieneuCatalog() async throws {
        let outcome = try await RealWorker.client.run(["voices"])

        let voices = try VoiceCatalog.voices(from: try #require(outcome.result))
        #expect(voices.count >= 20 && voices.allSatisfy { $0.engine == "vieneu" })
        #expect(voices.first?.featured == true && voices.allSatisfy { $0.wordsPerSec > 2 })
        #expect(outcome.result?["default"]?["voice"]?.stringValue == "Hải Đăng")
    }

    @Test func voicePreviewMakesACachedSample() async throws {
        let cache = VoicePreviewCache(directory: try makeTempDir())
        let file = cache.url(engine: "vieneu", voice: "Đức Trí", rate: "+10%", text: "Xin chào.")

        let outcome = try await RealWorker.client.run(["voice-preview", "--engine", "vieneu", "--voice", "Đức Trí",
                                                       "--rate", "+10%", "--text", "Xin chào.", "--out", file.path])

        #expect(outcome.failure == nil)
        #expect(cache.cached(engine: "vieneu", voice: "Đức Trí", rate: "+10%", text: "Xin chào.") == file)
        #expect((outcome.result?["duration"]?.doubleValue ?? 0) > 0.3)
    }

    @Test func doctorChecksDecode() async throws {
        let outcome = try await RealWorker.client.run(["doctor", "--root", FileManager.default.temporaryDirectory.path])

        let checks = try DoctorCheck.list(from: try #require(outcome.result))
        #expect(Set(checks.map(\.name)).isSuperset(of: ["python", "ffmpeg", "claude"]))
    }

    @Test func newSetIngestAndCancelLikeTheApp() async throws {
        let root = try makeTempDir()
        // new + set (the new-project sheet)
        let created = try await RealWorker.client.run(["new", "--title", "Mẫu", "--source", RealWorker.sample.path, "--root", root.path])
        let dir = URL(filePath: try #require(created.result?["project"]?.stringValue))
        let set = try await RealWorker.client.run(["set", "--project", dir.path, "voice=Đức Trí", "tts_rate=+10%", "target_ratio=0.25"])
        #expect(set.failure == nil)
        let file = try ProjectFile.load(from: dir)
        #expect(file.settings.voice == "Đức Trí" && file.settings.ttsRate == "+10%" && file.settings.targetRatio == 0.25)
        let bad = try await RealWorker.client.run(["set", "--project", dir.path, "voice=Không Có"])
        #expect(bad.failure?.code == "settings_invalid")

        // ingest through the queue, with live progress (the pipeline view)
        let state = await MainActor.run { ProjectRunState() }
        let queue = await MainActor.run { JobQueue(launcher: RealWorker.client) }
        await MainActor.run {
            let request = JobRequest(projectDir: dir, arguments: ["ingest", "--project", dir.path], title: "ingest")
            state.begin(jobID: request.id, title: "ingest")
            queue.enqueue(request) { state.handle($0) }
        }
        try await waitForIdle(queue)
        let finished = await MainActor.run { state.finishedStages.map(\.stage) }
        #expect(finished == [.ingest])
        #expect(try ProjectFile.load(from: dir).state(of: .ingest).status == .done)
        #expect(try ProjectFile.load(from: dir).media?.width == 854)

        // a long stage cancelled from the UI stops cleanly and is recorded as failed/cancelled
        await MainActor.run {
            let request = JobRequest(projectDir: dir, arguments: ["transcribe", "--project", dir.path], title: "transcribe")
            state.begin(jobID: request.id, title: "transcribe")
            queue.enqueue(request) { event in
                state.handle(event)
                if event == .stageStart(stage: "transcribe") { queue.cancel(request.id) }
            }
        }
        try await waitForIdle(queue)
        let logs = await MainActor.run { state.logs.map(\.message) }
        #expect(logs.contains("Đã hủy"))
        let transcribe = try ProjectFile.load(from: dir).state(of: .transcribe)
        #expect(transcribe.status == .failed && transcribe.error?.code == "cancelled")
    }

    @MainActor
    private func waitForIdle(_ queue: JobQueue) async throws {
        for _ in 0..<1200 where queue.hasWork { try await Task.sleep(for: .milliseconds(100)) }
        #expect(!queue.hasWork)
    }
}
