import Foundation
import Testing
@testable import RecapKit

/// A fake worker: `/bin/sh -c <script>`; the arguments a real call would pass arrive as $1, $2...
func shellClient(_ script: String) -> WorkerClient {
    WorkerClient(config: WorkerConfig(
        executable: URL(filePath: "/bin/sh"),
        baseArguments: ["-c", script, "recap"],
        workingDirectory: FileManager.default.temporaryDirectory,
        extraPath: ["/opt/homebrew/bin"]
    ))
}

func collect(_ job: WorkerJob) async -> [WorkerEvent] {
    var events: [WorkerEvent] = []
    for await event in job.events { events.append(event) }
    return events
}

@Suite("WorkerClient: runs the worker and streams its events")
struct WorkerClientTests {
    @Test func streamsStdoutEventsStderrLinesAndExitCode() async throws {
        let job = try shellClient("""
        echo '{"type":"stage_start","stage":"tts"}'
        echo 'thô trên stderr' >&2
        echo '{"type":"stage_end","stage":"tts","status":"done","elapsed_sec":1.5}'
        exit 0
        """).start([])

        let events = await collect(job)

        #expect(events.contains(.stageStart(stage: "tts")))
        #expect(events.contains(.raw("thô trên stderr")))
        #expect(events.contains(.stageEnd(stage: "tts", status: "done", elapsedSec: 1.5, skipped: false)))
        #expect(events.last == .exit(code: 0))
    }

    @Test func passesArgumentsAndPathEnvironment() async throws {
        let job = try shellClient(#"echo "{\"type\":\"log\",\"level\":\"info\",\"message\":\"$1 $2 $PYTHONUNBUFFERED\"}"; echo "$PATH" >&2"#)
            .start(["tts", "--project"])

        let events = await collect(job)

        #expect(events.contains(.log(level: "info", message: "tts --project 1")))
        let path = events.compactMap { if case let .raw(line) = $0 { line } else { nil } }.first ?? ""
        #expect(path.hasPrefix("/opt/homebrew/bin:"))
    }

    @Test func cancelSendsSigtermAndTheWorkerCanCleanUp() async throws {
        let job = try shellClient("""
        trap 'echo "{\\"type\\":\\"log\\",\\"level\\":\\"info\\",\\"message\\":\\"dọn dẹp\\"}"; exit 130' TERM
        echo '{"type":"stage_start","stage":"describe"}'
        while true; do sleep 0.05; done
        """).start([])

        var events: [WorkerEvent] = []
        for await event in job.events {
            events.append(event)
            if event == .stageStart(stage: "describe") { job.cancel() }
        }

        #expect(events.contains(.log(level: "info", message: "dọn dẹp")))
        #expect(events.last == .exit(code: 130))
    }

    @Test func runCollectsResultOrError() async throws {
        let ok = try await shellClient(#"echo '{"type":"result","command":"voices","data":{"n":1}}'"#).run([])
        #expect(ok.result == .object(["n": .number(1)]))
        #expect(ok.failure == nil && ok.exitCode == 0)

        let bad = try await shellClient(#"echo '{"type":"error","stage":null,"code":"voice_invalid","message":"Không có giọng","retryable":false}'; exit 1"#).run([])
        #expect(bad.result == nil && bad.exitCode == 1)
        #expect(bad.failure == WorkerFailure(code: "voice_invalid", message: "Không có giọng", retryable: false))
    }

    @Test func missingExecutableThrows() {
        let client = WorkerClient(config: WorkerConfig(
            executable: URL(filePath: "/khong/co/python"), baseArguments: [], workingDirectory: URL(filePath: "/"), extraPath: []
        ))
        #expect(throws: (any Error).self) { try client.start([]) }
    }
}
