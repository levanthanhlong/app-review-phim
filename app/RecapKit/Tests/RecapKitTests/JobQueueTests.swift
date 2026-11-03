import Foundation
import Testing
@testable import RecapKit

/// Runs each request's first argument as a shell script.
struct ScriptLauncher: WorkerLauncher {
    func start(_ arguments: [String]) throws -> WorkerJob {
        try shellClient(arguments[0]).start([])
    }
}

struct FailingLauncher: WorkerLauncher {
    func start(_ arguments: [String]) throws -> WorkerJob {
        throw CocoaError(.fileNoSuchFile)
    }
}

@MainActor
final class Recorder {
    var lines: [String] = []

    func handler(_ name: String) -> @MainActor (WorkerEvent) -> Void {
        { [weak self] event in
            switch event {
            case let .log(_, message): self?.lines.append("\(name):\(message)")
            case let .exit(code): self?.lines.append("\(name):exit \(code)")
            case let .error(_, code, _, _): self?.lines.append("\(name):error \(code)")
            default: break
            }
        }
    }
}

func log(_ message: String) -> String {
    #"echo '{"type":"log","level":"info","message":"\#(message)"}'"#
}

@MainActor
func waitUntilIdle(_ queue: JobQueue) async throws {
    for _ in 0..<200 where queue.current != nil || !queue.pending.isEmpty {
        try await Task.sleep(for: .milliseconds(25))
    }
    #expect(queue.current == nil && queue.pending.isEmpty)
}

@MainActor
@Suite("JobQueue: one heavy job at a time")
struct JobQueueTests {
    let projectA = URL(filePath: "/tmp/A.recap")
    let projectB = URL(filePath: "/tmp/B.recap")

    @Test func jobsRunOneAfterAnother() async throws {
        let queue = JobQueue(launcher: ScriptLauncher())
        let recorder = Recorder()

        queue.enqueue(JobRequest(projectDir: projectA, arguments: ["sleep 0.3; " + log("a xong")], title: "A"), onEvent: recorder.handler("A"))
        queue.enqueue(JobRequest(projectDir: projectB, arguments: [log("b chạy")], title: "B"), onEvent: recorder.handler("B"))

        #expect(queue.current?.title == "A" && queue.pending.map(\.title) == ["B"])
        #expect(queue.isBusy(project: projectB) && !queue.isRunning(project: projectB))
        try await waitUntilIdle(queue)
        #expect(recorder.lines == ["A:a xong", "A:exit 0", "B:b chạy", "B:exit 0"])
    }

    @Test func cancellingTheRunningJobTerminatesIt() async throws {
        let queue = JobQueue(launcher: ScriptLauncher())
        let recorder = Recorder()
        let script = "trap 'exit 130' TERM; " + log("bắt đầu") + "; while true; do sleep 0.05; done"
        let request = JobRequest(projectDir: projectA, arguments: [script], title: "A")
        queue.enqueue(request, onEvent: recorder.handler("A"))

        try await Task.sleep(for: .milliseconds(300))
        queue.cancel(request.id)

        try await waitUntilIdle(queue)
        #expect(recorder.lines == ["A:bắt đầu", "A:exit 130"])
    }

    @Test func cancellingAPendingJobRemovesItWithoutRunning() async throws {
        let queue = JobQueue(launcher: ScriptLauncher())
        let recorder = Recorder()
        queue.enqueue(JobRequest(projectDir: projectA, arguments: ["sleep 0.2"], title: "A"), onEvent: recorder.handler("A"))
        let pending = JobRequest(projectDir: projectB, arguments: [log("không được chạy")], title: "B")
        queue.enqueue(pending, onEvent: recorder.handler("B"))

        queue.cancel(pending.id)

        #expect(queue.pending.isEmpty)
        try await waitUntilIdle(queue)
        #expect(recorder.lines == ["B:exit 130", "A:exit 0"])
    }

    @Test func launchFailureIsReportedAndTheQueueMovesOn() async throws {
        let queue = JobQueue(launcher: FailingLauncher())
        let recorder = Recorder()

        queue.enqueue(JobRequest(projectDir: projectA, arguments: ["x"], title: "A"), onEvent: recorder.handler("A"))
        queue.enqueue(JobRequest(projectDir: projectB, arguments: ["x"], title: "B"), onEvent: recorder.handler("B"))

        try await waitUntilIdle(queue)
        #expect(recorder.lines == ["A:error worker_unavailable", "A:exit -1", "B:error worker_unavailable", "B:exit -1"])
    }
}
