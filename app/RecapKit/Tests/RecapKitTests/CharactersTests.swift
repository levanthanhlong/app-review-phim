import Foundation
import Testing
@testable import RecapKit

@Suite("Characters found by the worker and the user's naming")
struct CharactersTests {
    let dir: URL

    init() throws {
        dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir.appending(path: "work"), withIntermediateDirectories: true)
    }

    private func write(_ text: String, _ path: String) throws {
        try Data(text.utf8).write(to: dir.appending(path: path))
    }

    @Test func reportListsPeoplePicturesAndNamesHeard() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(CharacterReport.load(from: dir) == nil)

        try write(#"""
        {"faces_key": "k", "characters": [
          {"id": "C01", "faces": 187, "screen_sec": 187.0, "beats": ["B0001", "B0002"],
           "pictures": ["characters/faces/f00001.jpg"]}],
         "names": [{"name": "Hiro", "count": 4, "examples": ["Hiro ơi"]}], "names_warning": null}
        """#, "work/characters.json")

        let report = try #require(CharacterReport.load(from: dir))
        #expect(report.characters.map(\.id) == ["C01"] && report.characters[0].faces == 187)
        #expect(report.characters[0].pictureURLs(in: dir).first?.path().hasSuffix("work/characters/faces/f00001.jpg") == true)
        #expect(report.names == [NameCandidate(name: "Hiro", count: 4, examples: ["Hiro ơi"])])
        #expect(report.namesWarning == nil)
    }

    @Test func missingOrDamagedChoicesMeanNothingChosen() throws {
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(CharacterChoices.load(from: dir) == CharacterChoices())

        try write("not json", "work/character_choices.json")
        #expect(CharacterChoices.load(from: dir) == CharacterChoices())

        try write(#"{"confirmed": true, "names": {"C01": "Pan"}, "merged": {"C06": "C01"}, "ignored": ["C08"]}"#,
                  "work/character_choices.json")
        #expect(CharacterChoices.load(from: dir)
            == CharacterChoices(confirmed: true, names: ["C01": "Pan"], merged: ["C06": "C01"], ignored: ["C08"]))
    }

    @Test func savePayloadMatchesWhatTheWorkerExpects() throws {
        let choices = CharacterChoices(confirmed: true, names: ["C01": "Pan"], merged: ["C06": "C01"], ignored: ["C08"])
        let payload = try #require(JSONSerialization.jsonObject(with: choices.savePayload()) as? [String: Any])
        #expect(payload["names"] as? [String: String] == ["C01": "Pan"])
        #expect(payload["merged"] as? [String: String] == ["C06": "C01"])
        #expect(payload["ignored"] as? [String] == ["C08"])

        let skip = try #require(JSONSerialization.jsonObject(with: CharacterChoices.skipPayload()) as? [String: Any])
        #expect(skip["skip"] as? Bool == true)
    }

    @Test func genericLabelsCoverTheUsersListAndJobs() {
        for label in ["người đàn ông", "người phụ nữ", "cô gái", "chàng trai", "người đi đường", "người mẹ",
                      "con ma", "sinh vật lạ", "cậu bé", "bác sĩ", "cảnh sát"] {
            #expect(CharacterLabels.defaults.contains(label), "\(label)")
        }
        #expect(Set(CharacterLabels.defaults).count == CharacterLabels.defaults.count)
    }

    @Test func editsReturnNewChoicesAndKeepTheOld() {
        let start = CharacterChoices()
        let named = start.naming("C01", "  Pan ").merging("C06", into: "C01").ignoring("C08", true)
        #expect(start == CharacterChoices())
        #expect(named.names == ["C01": "Pan"] && named.merged == ["C06": "C01"] && named.ignored == ["C08"])

        let cleared = named.naming("C01", " ").merging("C06", into: nil).ignoring("C08", false)
        #expect(cleared.names.isEmpty && cleared.merged.isEmpty && cleared.ignored.isEmpty)
    }

    @Test func mergedPeopleShowTheNameOfWhoTheyAre() {
        let choices = CharacterChoices(names: ["C01": "Pan"], merged: ["C06": "C07", "C07": "C01", "C02": "C03", "C03": "C02"])
        #expect(choices.resolved("C06") == "C01" && choices.displayName("C06") == "Pan")
        #expect(choices.resolved("C02") == "C02")  // a merge loop keeps the character itself
        #expect(choices.displayName("C04") == nil)
    }

    @Test func genderIsKeptSavedAndClearedLikeTheName() throws {
        let choices = CharacterChoices().naming("C01", "Cha").gendering("C01", "nữ")
        #expect(choices.genders == ["C01": "nữ"])
        #expect(choices.gendering("C01", nil).genders.isEmpty)

        let payload = try #require(JSONSerialization.jsonObject(with: choices.savePayload()) as? [String: Any])
        #expect(payload["genders"] as? [String: String] == ["C01": "nữ"])

        try write(#"{"confirmed": true, "names": {"C01": "Cha"}, "genders": {"C01": "nữ"}}"#, "work/character_choices.json")
        #expect(CharacterChoices.load(from: dir).genders == ["C01": "nữ"])
        try? FileManager.default.removeItem(at: dir)
    }
}
