import Foundation

/// One audio file of the background-music folder.
public struct MusicTrack: Identifiable, Sendable, Equatable, Hashable {
    public let id: URL
    public let genre: String
    public var title: String { id.deletingPathExtension().lastPathComponent }
}

/// A sub-folder of the music folder; its name is the genre the user sorts tracks by.
public struct MusicGenre: Identifiable, Sendable, Equatable {
    public let name: String
    public let tracks: [MusicTrack]
    public var id: String { name }
}

public enum MusicLibrary {
    /// Same list as the worker's `recap.music.MUSIC_EXTENSIONS`.
    static let extensions: Set<String> = ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "aif", "aiff"]
    public static let otherGenre = "Khác"
    public static let defaultGenres = [
        "Hành động", "Hồi hộp - Kinh dị", "Hài hước", "Tình cảm", "Buồn - Sâu lắng", "Gia đình - Hoạt hình", "Sử thi - Hoành tráng",
    ]

    /// Genres (sub-folders, empty ones included) sorted by name, then loose files under "Khác".
    public static func scan(root: URL) -> [MusicGenre] {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey],
                                                      options: [.skipsHiddenFiles]) else { return [] }
        let folders = items.filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
        var genres = folders
            .map { MusicGenre(name: $0.lastPathComponent, tracks: tracks(in: $0, genre: $0.lastPathComponent)) }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        let loose = items.filter { isAudio($0) }.map { MusicTrack(id: $0, genre: otherGenre) }
        if !loose.isEmpty { genres.append(MusicGenre(name: otherGenre, tracks: sorted(loose))) }
        return genres
    }

    /// Creates the folder with the default genre sub-folders, only when it does not exist yet.
    public static func prepare(root: URL) throws {
        guard !FileManager.default.fileExists(atPath: root.path) else { return }
        for genre in defaultGenres {
            try FileManager.default.createDirectory(at: root.appending(path: genre), withIntermediateDirectories: true)
        }
    }

    public static func track(atPath path: String, in genres: [MusicGenre]) -> MusicTrack? {
        let wanted = URL(filePath: path).standardizedFileURL.path
        return genres.lazy.flatMap(\.tracks).first { $0.id.standardizedFileURL.path == wanted }
    }

    private static func tracks(in folder: URL, genre: String) -> [MusicTrack] {
        let walker = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])
        let files = (walker?.allObjects as? [URL]) ?? []
        return sorted(files.filter(isAudio).map { MusicTrack(id: $0, genre: genre) })
    }

    private static func isAudio(_ url: URL) -> Bool {
        extensions.contains(url.pathExtension.lowercased())
    }

    private static func sorted(_ tracks: [MusicTrack]) -> [MusicTrack] {
        tracks.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }
}
