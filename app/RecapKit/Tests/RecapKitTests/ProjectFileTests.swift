import Foundation
import Testing
@testable import RecapKit

let sampleProjectJSON = #"""
{
 "id": "4bdfdcb3-d993-4e44-bdff-cae74b358bf0",
 "title": "Cóc Kiện Trời",
 "source_path": "/x/clip.mp4",
 "created_at": "2026-11-03T03:45:26+00:00",
 "settings": {"target_ratio": 0.2, "narration_style": "kich_tinh", "tts_engine": "vieneu", "voice": "Thiện Minh",
              "tts_rate": "+0%", "duck_volume": 0.15, "subtitle_font": "Arial", "future_setting": 1},
 "media": {"duration": 446.75, "width": 854, "height": 480, "fps": 25.0, "audio_tracks": []},
 "stages": {
  "ingest": {"status": "done", "progress": 1.0, "elapsed_sec": 7.6, "error": null, "fingerprint": "x"},
  "transcribe": {"status": "done", "progress": 1.0, "elapsed_sec": 42.8, "error": null},
  "scenes": {"status": "stale", "progress": 1.0},
  "describe": {"status": "failed", "progress": 0.0,
               "error": {"code": "ollama_unavailable", "message": "Ollama chưa chạy", "retryable": true}},
  "script": {"status": "pending", "progress": 0.0},
  "tts": {"status": "pending", "progress": 0.0},
  "assemble": {"status": "pending", "progress": 0.0},
  "export": {"status": "running", "progress": 0.0}
 }
}
"""#

@Suite("project.json model")
struct ProjectFileTests {
    let project: ProjectFile

    init() throws {
        project = try ProjectFile.decode(Data(sampleProjectJSON.utf8))
    }

    @Test func decodesTitleSettingsAndMedia() {
        #expect(project.title == "Cóc Kiện Trời")
        #expect(project.settings.voice == "Thiện Minh")
        #expect(project.settings.targetRatio == 0.2)
        #expect(project.media?.duration == 446.75)
        #expect(project.targetDuration == 446.75 * 0.2)
    }

    @Test func stagesComeInPipelineOrder() {
        #expect(project.orderedStages.map(\.name) == StageName.allCases)
        #expect(project.state(of: .describe).error?.code == "ollama_unavailable")
        #expect(project.state(of: .describe).error?.retryable == true)
        #expect(project.state(of: .scenes).status == .stale)
    }

    @Test func overallStatusPrefersFailures() {
        #expect(project.overallStatus(isRunning: false) == .failed)
    }

    @Test func runningStageWithoutAProcessIsInterrupted() {
        #expect(project.displayStatus(of: .export, isRunning: false) == .interrupted)
        #expect(project.displayStatus(of: .export, isRunning: true) == .running)
        #expect(project.displayStatus(of: .ingest, isRunning: false) == .done)
    }

    @Test func missingStagesAreTreatedAsPending() throws {
        let minimal = #"{"id":"1","title":"A","source_path":"/a","created_at":"x","settings":{},"stages":{}}"#
        let file = try ProjectFile.decode(Data(minimal.utf8))
        #expect(file.state(of: .tts).status == .pending)
        #expect(file.overallStatus(isRunning: false) == .pending)
        #expect(file.settings.voice == nil)
    }

    @Test func overallStatusWhenEverythingIsDone() throws {
        let stages = StageName.allCases.map { "\"\($0.rawValue)\": {\"status\": \"done\"}" }.joined(separator: ",")
        let json = #"{"id":"1","title":"A","source_path":"/a","created_at":"x","settings":{},"stages":{"# + stages + "}}"
        #expect(try ProjectFile.decode(Data(json.utf8)).overallStatus(isRunning: false) == .done)
    }

    @Test func stagesFollowTheWorkerOrderWithMusicBeforeAssemble() {
        #expect(StageName.allCases.map(\.rawValue) == [
            "ingest", "transcribe", "scenes", "inspect", "characters", "describe", "script", "tts", "music", "assemble", "export",
        ])
    }
}
