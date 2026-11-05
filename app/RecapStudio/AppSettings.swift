import Foundation
import Observation
import RecapKit

/// Global settings (spec §7.1), persisted in UserDefaults.
@MainActor
@Observable
final class AppSettings {
    static let defaultRepo = "/Volumes/ExternalSSD/myapps/appMReviewphim"
    static let defaultToolPath = "/opt/homebrew/bin:/usr/local/bin"

    private enum Key {
        static let worker = "workerDirectory"
        static let python = "pythonPath"
        static let projects = "projectsRoot"
        static let tools = "toolPath"
        static let music = "musicRoot"
        static let brand = "brandFolder"
        static let labels = "characterLabels"
    }

    @ObservationIgnored private let defaults: UserDefaults

    /// `worker/` folder of the repo (contains the `recap` package and its .venv).
    var workerDirectory: String { didSet { defaults.set(workerDirectory, forKey: Key.worker) } }
    /// Python of the worker venv.
    var pythonPath: String { didSet { defaults.set(pythonPath, forKey: Key.python) } }
    /// Where `<Title>.recap` project folders live.
    var projectsRoot: String { didSet { defaults.set(projectsRoot, forKey: Key.projects) } }
    /// Folders with ffmpeg, claude and ollama, prepended to PATH (apps do not get the shell PATH).
    var toolPath: String { didSet { defaults.set(toolPath, forKey: Key.tools) } }
    /// Background-music folder: one sub-folder per genre (see `MusicLibrary`).
    var musicRoot: String { didSet { defaults.set(musicRoot, forKey: Key.music) } }
    /// Channel images: "Banner dưới video…" and "Ảnh đại diện…" (png).
    var brandFolder: String { didSet { defaults.set(brandFolder, forKey: Key.brand) } }
    /// Names that fit anyone ("cô gái", "bác sĩ"...), offered in the "Nhân vật" tab; the user edits the list.
    var characterLabels: [String] { didSet { defaults.set(characterLabels, forKey: Key.labels) } }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let worker = defaults.string(forKey: Key.worker) ?? "\(Self.defaultRepo)/worker"
        workerDirectory = worker
        pythonPath = defaults.string(forKey: Key.python) ?? "\(worker)/.venv/bin/python"
        projectsRoot = defaults.string(forKey: Key.projects) ?? "\(Self.defaultRepo)/output"
        toolPath = defaults.string(forKey: Key.tools) ?? Self.defaultToolPath
        // next to worker/ in the repo, so it follows wherever the repo lives
        musicRoot = defaults.string(forKey: Key.music) ?? "\((worker as NSString).deletingLastPathComponent)/music"
        brandFolder = defaults.string(forKey: Key.brand) ?? "\((worker as NSString).deletingLastPathComponent)/img"
        characterLabels = defaults.stringArray(forKey: Key.labels) ?? CharacterLabels.defaults
    }

    var projectsRootURL: URL { URL(filePath: projectsRoot, directoryHint: .isDirectory) }
    var musicRootURL: URL { URL(filePath: musicRoot, directoryHint: .isDirectory) }

    /// The channel banner and logo found in `brandFolder` (by file-name prefix), if any.
    var brandImages: (banner: URL, logo: URL?)? {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: URL(filePath: brandFolder, directoryHint: .isDirectory), includingPropertiesForKeys: nil)) ?? []
        // Finder may store names decomposed (NFD): compare the precomposed form
        func first(_ prefix: String) -> URL? {
            files.filter { $0.lastPathComponent.precomposedStringWithCanonicalMapping.hasPrefix(prefix) }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }.first
        }
        guard let banner = first("Banner dưới video") else { return nil }
        return (banner, first("Ảnh đại diện"))
    }

    var workerConfig: WorkerConfig {
        WorkerConfig(
            executable: URL(filePath: pythonPath),
            baseArguments: ["-m", "recap"],
            workingDirectory: URL(filePath: workerDirectory, directoryHint: .isDirectory),
            extraPath: toolPath.split(separator: ":").map(String.init).filter { !$0.isEmpty }
        )
    }
}

/// A launcher whose config can change at runtime (Settings window) while the queue keeps running.
final class ConfigurableLauncher: WorkerLauncher, @unchecked Sendable {
    private let lock = NSLock()
    private var config: WorkerConfig

    init(config: WorkerConfig) {
        self.config = config
    }

    func update(_ config: WorkerConfig) {
        lock.withLock { self.config = config }
    }

    var client: WorkerClient {
        lock.withLock { WorkerClient(config: config) }
    }

    func start(_ arguments: [String]) throws -> WorkerJob {
        try client.start(arguments)
    }
}
