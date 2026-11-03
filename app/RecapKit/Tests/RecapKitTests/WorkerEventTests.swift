import Foundation
import Testing
@testable import RecapKit

@Suite("WorkerEvent decoding (JSON Lines protocol, spec §6)")
struct WorkerEventTests {
    @Test func decodesStageStartAndProgress() {
        #expect(WorkerEvent.decode(line: #"{"type":"stage_start","stage":"describe","total":742}"#)
            == .stageStart(stage: "describe"))
        #expect(WorkerEvent.decode(line: #"{"type":"progress","stage":"describe","done":120,"total":742,"message":"B0120"}"#)
            == .progress(stage: "describe", done: 120, total: 742, message: "B0120"))
    }

    @Test func decodesLogArtifactAndStageEnd() {
        #expect(WorkerEvent.decode(line: #"{"type":"log","level":"warning","message":"Thử lại"}"#)
            == .log(level: "warning", message: "Thử lại"))
        #expect(WorkerEvent.decode(line: #"{"type":"artifact","stage":"scenes","path":"work/scenes.json"}"#)
            == .artifact(stage: "scenes", path: "work/scenes.json"))
        #expect(WorkerEvent.decode(line: #"{"type":"stage_end","stage":"tts","status":"done","elapsed_sec":16.2,"skipped":true}"#)
            == .stageEnd(stage: "tts", status: "done", elapsedSec: 16.2, skipped: true))
    }

    @Test func decodesErrorWithAndWithoutStage() {
        #expect(WorkerEvent.decode(line: #"{"type":"error","stage":"script","code":"llm_quota","message":"Hết","retryable":true}"#)
            == .error(stage: "script", code: "llm_quota", message: "Hết", retryable: true))
        #expect(WorkerEvent.decode(line: #"{"type":"error","stage":null,"code":"voice_invalid","message":"x","retryable":false}"#)
            == .error(stage: nil, code: "voice_invalid", message: "x", retryable: false))
    }

    @Test func decodesResultWithArbitraryData() {
        let event = WorkerEvent.decode(line: #"{"type":"result","command":"new","data":{"project":"/a/b.recap","n":[1,2.5,null,true]}}"#)
        guard case let .result(command, data) = event else {
            Issue.record("not a result: \(String(describing: event))")
            return
        }
        #expect(command == "new")
        #expect(data["project"]?.stringValue == "/a/b.recap")
        #expect(data["n"] == .array([.number(1), .number(2.5), .null, .bool(true)]))
    }

    @Test func nonJSONOrUnknownLinesAreRaw() {
        #expect(WorkerEvent.decode(line: "Detected language: vi") == .raw("Detected language: vi"))
        #expect(WorkerEvent.decode(line: #"{"type":"mystery"}"#) == .raw(#"{"type":"mystery"}"#))
    }
}
