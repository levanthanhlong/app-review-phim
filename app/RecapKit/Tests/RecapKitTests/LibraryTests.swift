import Foundation
import Testing
@testable import RecapKit

func makeTempDir() throws -> URL {
    let dir = FileManager.default.temporaryDirectory.appending(path: "recapkit-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

func writeProject(_ root: URL, folder: String, title: String, created: String, keyframe: Bool = false) throws -> URL {
    let dir = root.appending(path: folder)
    try FileManager.default.createDirectory(at: dir.appending(path: "work/keyframes"), withIntermediateDirectories: true)
    let json = #"{"id":"\#(folder)","title":"\#(title)","source_path":"/a.mp4","created_at":"\#(created)","settings":{},"stages":{}}"#
    try json.write(to: dir.appending(path: "project.json"), atomically: true, encoding: .utf8)
    if keyframe { try Data([0xFF]).write(to: dir.appending(path: "work/keyframes/B0001.jpg")) }
    return dir
}

@Suite("Project library, voices, previews, formatting")
struct LibraryTests {
    @Test func scanFindsRecapFoldersNewestFirstAndSkipsBrokenOnes() throws {
        let root = try makeTempDir()
        _ = try writeProject(root, folder: "Cũ.recap", title: "Cũ", created: "2026-01-01T00:00:00+00:00")
        let newer = try writeProject(root, folder: "Mới.recap", title: "Mới", created: "2026-11-03T00:00:00+00:00", keyframe: true)
        try FileManager.default.createDirectory(at: root.appending(path: "Hỏng.recap"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: root.appending(path: "voice_samples"), withIntermediateDirectories: true)

        let projects = ProjectLibrary.scan(root: root)

        #expect(projects.map(\.file.title) == ["Mới", "Cũ"])
        #expect(projects[0].id == newer.standardizedFileURL)
        #expect(projects[0].thumbnail?.lastPathComponent == "B0001.jpg")
        #expect(projects[1].thumbnail == nil)
    }

    @Test func scanOfMissingRootIsEmpty() {
        #expect(ProjectLibrary.scan(root: URL(filePath: "/khong/co/thu/muc")).isEmpty)
    }

    @Test func decodesVoiceListFromTheVoicesResult() throws {
        let data = JSONValue.object(["voices": .array([.object([
            "engine": .string("vieneu"), "id": .string("Đức Trí"), "description": .string("Nam · Nam · Phong cách đọc truyện"),
            "gender": .string("male"), "region": .string("Nam"), "featured": .bool(false), "words_per_sec": .number(3.12),
        ])])])

        let voices = try VoiceCatalog.voices(from: data)

        #expect(voices == [Voice(engine: "vieneu", id: "Đức Trí", description: "Nam · Nam · Phong cách đọc truyện",
                                 gender: "male", region: "Nam", featured: false, wordsPerSec: 3.12)])
        #expect(voices[0].isMale && voices[0].label == "Đức Trí")
    }

    @Test func previewCacheKeysOnEveryInput() throws {
        let cache = VoicePreviewCache(directory: try makeTempDir())

        let a = cache.url(engine: "vieneu", voice: "Hải Đăng", rate: "+0%", text: "Xin chào")
        #expect(a == cache.url(engine: "vieneu", voice: "Hải Đăng", rate: "+0%", text: "Xin chào"))
        #expect(a != cache.url(engine: "vieneu", voice: "Hải Đăng", rate: "+10%", text: "Xin chào"))
        #expect(a != cache.url(engine: "vieneu", voice: "Đức Trí", rate: "+0%", text: "Xin chào"))
        #expect(a.pathExtension == "wav")
        #expect(cache.cached(engine: "vieneu", voice: "Hải Đăng", rate: "+0%", text: "Xin chào") == nil)
        try Data([1]).write(to: a)
        #expect(cache.cached(engine: "vieneu", voice: "Hải Đăng", rate: "+0%", text: "Xin chào") == a)
    }

    @Test(arguments: [(0.0, "0:00"), (65.4, "1:05"), (446.75, "7:27"), (3725.0, "1:02:05")])
    func clockFormat(seconds: Double, text: String) {
        #expect(Durations.clock(seconds) == text)
    }

    @Test func remainingTimeNeedsSomeProgress() {
        #expect(Durations.remaining(elapsed: 30, fraction: 0.25) == 90)
        #expect(Durations.remaining(elapsed: 1, fraction: 0.01) == nil)  // too early to guess
        #expect(Durations.remaining(elapsed: 10, fraction: 1) == 0)
    }

    @Test func rateTextRoundTrips() {
        #expect(Durations.rateText(percent: 0) == "+0%")
        #expect(Durations.rateText(percent: -15) == "-15%")
        #expect(Durations.ratePercent("+10%") == 10)
        #expect(Durations.ratePercent("abc") == 0)
    }
}
