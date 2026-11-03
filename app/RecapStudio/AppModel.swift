import AppKit
import AVFoundation
import Foundation
import Observation
import RecapKit
import UserNotifications

struct AppError: LocalizedError {
    let message: String
    var errorDescription: String? { message }

    init(_ message: String) { self.message = message }
    init(_ failure: WorkerFailure) { message = failure.message }
}

/// App-wide state: settings, the project list, the job queue and the per-project run state.
@MainActor
@Observable
final class AppModel {
    static let notifyAfterSec = 60.0
    static let refreshInterval: Duration = .seconds(2)

    let settings = AppSettings()
    let queue: JobQueue
    private(set) var projects: [ProjectSummary] = []
    private(set) var voices: [Voice] = []
    private(set) var voicesError: String?
    private(set) var previewing: Voice.ID?
    private(set) var musicGenres: [MusicGenre] = []
    private(set) var musicError: String?
    private(set) var playingMusic: URL?  // file being previewed (library track or AI option)

    @ObservationIgnored private let launcher: ConfigurableLauncher
    @ObservationIgnored private var runStates: [URL: ProjectRunState] = [:]
    @ObservationIgnored private var player: AVAudioPlayer?
    @ObservationIgnored private var musicPlayer: AVAudioPlayer?
    @ObservationIgnored private var previewCache: VoicePreviewCache

    init() {
        launcher = ConfigurableLauncher(config: settings.workerConfig)
        queue = JobQueue(launcher: launcher)
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        previewCache = VoicePreviewCache(directory: caches.appending(path: "RecapStudio/voice-previews"))
        reload()
        Task { [weak self] in
            while let self {
                try? await Task.sleep(for: Self.refreshInterval)
                self.reload()
            }
        }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private var client: WorkerClient { launcher.client }

    func settingsChanged() {
        launcher.update(settings.workerConfig)
        voices = []
        reload()
    }

    func reload() {
        let scanned = ProjectLibrary.scan(root: settings.projectsRootURL)
        if scanned != projects { projects = scanned }
    }

    func project(_ id: URL?) -> ProjectSummary? {
        projects.first { $0.id == id }
    }

    func runState(_ project: URL) -> ProjectRunState {
        if let state = runStates[project] { return state }
        let state = ProjectRunState()
        runStates[project] = state
        return state
    }

    // MARK: - Pipeline jobs (one at a time, through the queue)

    func runAll(_ project: ProjectSummary) {
        enqueue(project, ["run", "--project", project.id.path], title: "Chạy toàn bộ")
    }

    func runFrom(_ stage: StageName, _ project: ProjectSummary) {
        enqueue(project, ["run", "--project", project.id.path, "--from", stage.rawValue], title: "Chạy từ \(stage.label)")
    }

    /// One stage, reusing what is still valid (no `--force`): e.g. music after a prompt edit.
    func run(_ stage: StageName, _ project: ProjectSummary) {
        enqueue(project, [stage.rawValue, "--project", project.id.path], title: stage.label)
    }

    func rerun(_ stage: StageName, _ project: ProjectSummary) {
        enqueue(project, [stage.rawValue, "--project", project.id.path, "--force"], title: "Chạy lại \(stage.label)")
    }

    func cancel(_ project: ProjectSummary) {
        if let job = runState(project.id).jobID { queue.cancel(job) }
    }

    private func enqueue(_ project: ProjectSummary, _ arguments: [String], title: String) {
        let request = JobRequest(projectDir: project.id, arguments: arguments, title: "\(project.file.title): \(title)")
        let state = runState(project.id)
        state.begin(jobID: request.id, title: title)
        queue.enqueue(request) { [weak self, weak state] event in
            state?.handle(event)
            self?.after(event, project: project.file.title)
        }
    }

    private func after(_ event: WorkerEvent, project title: String) {
        switch event {
        case let .stageEnd(stage, status, elapsed, skipped):
            reload()
            if !skipped, elapsed >= Self.notifyAfterSec, !NSApp.isActive {
                let name = StageName(rawValue: stage)?.label ?? stage
                notify(title: title, body: status == "done" ? "Xong: \(name)" : "Lỗi ở bước \(name)")
            }
        case .exit, .stageStart:
            reload()
        default:
            break
        }
    }

    private func notify(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    // MARK: - Short commands (outside the queue)

    /// `recap new` then `recap set` for the chosen settings. Returns the new project folder.
    func createProject(source: URL, title: String, updates: [String: String]) async throws -> URL {
        let created = try await client.run(["new", "--title", title, "--source", source.path, "--root", settings.projectsRoot])
        if let failure = created.failure { throw AppError(failure) }
        guard let path = created.result?["project"]?.stringValue else { throw AppError("Worker không trả về thư mục dự án") }
        let dir = URL(filePath: path, directoryHint: .isDirectory).standardizedFileURL
        if !updates.isEmpty { _ = try await setSettings(dir, updates) }
        reload()
        return dir
    }

    /// `recap set`; returns the stages marked stale.
    func setSettings(_ project: URL, _ updates: [String: String]) async throws -> [String] {
        let assignments = updates.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
        let outcome = try await client.run(["set", "--project", project.path] + assignments)
        if let failure = outcome.failure { throw AppError(failure) }
        reload()
        let stale = outcome.result?["stale"].flatMap { try? $0.decode([String].self) }
        return stale ?? []
    }

    func loadVoices() async {
        guard voices.isEmpty else { return }
        do {
            let outcome = try await client.run(["voices"])
            if let failure = outcome.failure { throw AppError(failure) }
            voices = try VoiceCatalog.voices(from: outcome.result ?? .null)
            voicesError = nil
        } catch {
            voicesError = "Không tải được danh sách giọng: \(error.localizedDescription)"
        }
    }

    /// Plays a sample of `voice` (made once by `recap voice-preview`, then cached).
    func playPreview(_ voice: Voice, rate: String, text: String) async throws {
        previewing = voice.id
        defer { previewing = nil }
        let file = previewCache.url(engine: voice.engine, voice: voice.id, rate: rate, text: text)
        if previewCache.cached(engine: voice.engine, voice: voice.id, rate: rate, text: text) == nil {
            var args = ["voice-preview", "--engine", voice.engine, "--voice", voice.id, "--rate", rate, "--out", file.path]
            if !text.isEmpty { args += ["--text", text] }
            let outcome = try await client.run(args)
            if let failure = outcome.failure { throw AppError(failure) }
        }
        player?.stop()
        player = try AVAudioPlayer(contentsOf: file)
        player?.play()
    }

    // MARK: - AI music (plan edits through the worker)

    /// `recap music-choose`; returns the stages marked stale.
    func chooseMusic(_ project: URL, cue: String, option: Int) async throws -> [String] {
        try await musicCommand(["music-choose", "--project", project.path, "--cue", cue, "--option", String(option)])
    }

    /// `recap music-edit`: new prompts and/or new seeds for one cue (generated by the next music run).
    func editMusic(_ project: URL, cue: String, prompt: String?, altPrompt: String?, reroll: Bool) async throws -> [String] {
        var args = ["music-edit", "--project", project.path, "--cue", cue]
        if let prompt { args += ["--prompt", prompt] }
        if let altPrompt { args += ["--alt-prompt", altPrompt] }
        if reroll { args.append("--reroll") }
        return try await musicCommand(args)
    }

    private func musicCommand(_ args: [String]) async throws -> [String] {
        let outcome = try await client.run(args)
        if let failure = outcome.failure { throw AppError(failure) }
        reload()
        return outcome.result?["stale"].flatMap { try? $0.decode([String].self) } ?? []
    }

    // MARK: - Background music

    /// Scans the music folder; the first time, creates it with the default genre sub-folders.
    func loadMusic() {
        do {
            try MusicLibrary.prepare(root: settings.musicRootURL)
            musicGenres = MusicLibrary.scan(root: settings.musicRootURL)
            musicError = nil
        } catch {
            musicGenres = []
            musicError = "Không tạo được thư mục nhạc \(settings.musicRoot): \(error.localizedDescription)"
        }
    }

    /// Plays the music file from the start, or stops it if it is the one playing.
    func toggleMusicPreview(_ file: URL) throws {
        let wasPlaying = playingMusic == file
        stopMusicPreview()
        guard !wasPlaying else { return }
        musicPlayer = try AVAudioPlayer(contentsOf: file)
        musicPlayer?.play()
        playingMusic = file
    }

    func stopMusicPreview() {
        musicPlayer?.stop()
        musicPlayer = nil
        playingMusic = nil
    }

    func doctor() async throws -> [DoctorCheck] {
        let outcome = try await client.run(["doctor", "--root", settings.projectsRoot])
        guard let result = outcome.result else {
            throw outcome.failure.map(AppError.init) ?? AppError("Doctor không trả kết quả")
        }
        return try DoctorCheck.list(from: result)
    }
}
