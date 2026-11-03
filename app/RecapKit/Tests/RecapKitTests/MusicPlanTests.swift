import Foundation
import Testing
@testable import RecapKit

let samplePlanJSON = #"""
{
 "plan_key": "abc",
 "model": "medium",
 "cues": [
  {"id": "M01", "first_line": "L0001", "last_line": "L0010", "out_start": 0.0, "out_end": 51.54,
   "mood": "hồi hộp", "prompt": "tense strings", "alt_prompt": "sparse piano", "chosen": 2, "round": 0,
   "options": [
    {"label": "A", "prompt": "tense strings", "model_prompt": "tense strings, instrumental, no vocals", "seed": 11,
     "seconds": 54.5, "engine": "stable-audio-3", "model": "sa3 medium", "file": "music/M01_A.wav",
     "sha256": "aa", "created_at": "2026-11-03T16:00:00+00:00"},
    {"label": "B", "prompt": "tense strings", "seed": 12, "seconds": 54.5, "engine": "stable-audio-3",
     "model": "sa3 medium", "file": "music/M01_B.wav"},
    {"label": "C", "prompt": "sparse piano", "seed": 11, "seconds": 54.5, "engine": "ace-step-1.5",
     "model": "ace", "file": "music/M01_C.wav"}
   ]},
  {"id": "M02", "first_line": "L0011", "last_line": "L0018", "out_start": 51.54, "out_end": 96.64,
   "mood": "cao trào", "prompt": "triumphant brass", "alt_prompt": "playful woodwinds", "chosen": 0, "options": []}
 ]
}
"""#

@Suite("AI music plan (work/music/plan.json)")
struct MusicPlanTests {
    @Test func decodesCuesAndOptions() throws {
        let plan = try MusicPlan.decode(Data(samplePlanJSON.utf8))

        #expect(plan.cues.map(\.id) == ["M01", "M02"])
        let cue = plan.cues[0]
        #expect(cue.outEnd == 51.54 && cue.altPrompt == "sparse piano" && cue.chosen == 2)
        #expect(cue.options.map(\.label) == ["A", "B", "C"])
        #expect(cue.chosenOption?.engine == "ace-step-1.5")
        #expect(plan.cues[1].chosenOption == nil)  // not generated yet
    }

    @Test func optionFilesResolveUnderWork() throws {
        let plan = try MusicPlan.decode(Data(samplePlanJSON.utf8))
        let project = URL(filePath: "/p/A.recap", directoryHint: .isDirectory)

        #expect(plan.cues[0].options[0].url(in: project).path == "/p/A.recap/work/music/M01_A.wav")
    }

    @Test func missingPlanIsNil() throws {
        let dir = try makeTempDir()

        #expect(MusicPlan.load(from: dir) == nil)
    }
}
