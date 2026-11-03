import Foundation
import Testing
@testable import RecapKit

@Suite("Background music library")
struct MusicLibraryTests {
    private func touch(_ url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data([0]).write(to: url)
    }

    @Test func subfoldersAreGenresAndLooseFilesGoToOther() throws {
        let root = try makeTempDir()
        try touch(root.appending(path: "Kinh dị/Bóng tối.mp3"))
        try touch(root.appending(path: "Kinh dị/sub/Tiếng gió.m4a"))  // nested: still the folder's genre
        try touch(root.appending(path: "Hài hước/Vui.wav"))
        try touch(root.appending(path: "Hài hước/ghi chú.txt"))  // not audio
        try touch(root.appending(path: "Lẻ.flac"))
        try FileManager.default.createDirectory(at: root.appending(path: "Tình cảm"), withIntermediateDirectories: true)

        let genres = MusicLibrary.scan(root: root)

        #expect(genres.map(\.name) == ["Hài hước", "Kinh dị", "Tình cảm", MusicLibrary.otherGenre])
        #expect(genres[0].tracks.map(\.title) == ["Vui"])
        #expect(genres[1].tracks.map(\.title) == ["Bóng tối", "Tiếng gió"])
        #expect(genres[2].tracks.isEmpty)  // empty genre folders are shown so the user sees where to add music
        #expect(genres[3].tracks.first?.genre == MusicLibrary.otherGenre)
    }

    @Test func missingRootIsEmpty() {
        #expect(MusicLibrary.scan(root: URL(filePath: "/khong/co/thu/muc/nhac")).isEmpty)
    }

    @Test func prepareCreatesGenreFoldersOnlyWhenTheRootIsMissing() throws {
        let root = try makeTempDir().appending(path: "music")

        try MusicLibrary.prepare(root: root)
        let created = MusicLibrary.scan(root: root).map(\.name)
        try FileManager.default.removeItem(at: root.appending(path: MusicLibrary.defaultGenres[0]))
        try MusicLibrary.prepare(root: root)  // already exists: the user's layout is left alone

        #expect(Set(created) == Set(MusicLibrary.defaultGenres))
        #expect(MusicLibrary.scan(root: root).count == MusicLibrary.defaultGenres.count - 1)
    }

    @Test func trackLookupByPath() throws {
        let root = try makeTempDir()
        let song = root.appending(path: "Hành động/Đuổi bắt.mp3")
        try touch(song)
        let genres = MusicLibrary.scan(root: root)

        #expect(MusicLibrary.track(atPath: song.path, in: genres)?.title == "Đuổi bắt")
        #expect(MusicLibrary.track(atPath: "/khac/bai.mp3", in: genres) == nil)
    }

    @Test func projectSettingsDecodeMusic() throws {
        let json = #"{"id":"x","title":"T","source_path":"/a.mp4","created_at":"c","stages":{},"settings":{"music_file":"/m/a.mp3","music_volume":0.1}}"#
        let file = try ProjectFile.decode(Data(json.utf8))
        #expect(file.settings.musicFile == "/m/a.mp3")
        #expect(file.settings.musicVolume == 0.1)
    }
}
