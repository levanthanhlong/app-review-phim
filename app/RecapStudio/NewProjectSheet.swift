import AppKit
import RecapKit
import SwiftUI
import UniformTypeIdentifiers

/// Narration styles of the worker's script stage.
enum NarrationStyle: String, CaseIterable, Identifiable {
    case kichTinh = "kich_tinh"
    case nghiemTuc = "nghiem_tuc"
    case haiMiaMai = "hai_mia_mai"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .kichTinh: "Kịch tính, lôi cuốn"
        case .nghiemTuc: "Nghiêm túc, mạch lạc"
        case .haiMiaMai: "Hài hước, mỉa mai"
        }
    }

    var shortLabel: String {
        switch self {
        case .kichTinh: "Kịch tính"
        case .nghiemTuc: "Nghiêm túc"
        case .haiMiaMai: "Hài mỉa mai"
        }
    }
}

/// The settings both sheets edit; sent to the worker as `recap set` assignments.
struct EditableSettings: Equatable {
    static let ratioRange = 0.1...0.4
    static let ratioStep = 0.05

    var targetRatio = 0.2
    var style = NarrationStyle.kichTinh
    var voice = "Hải Đăng"
    var ratePercent = 0
    var duckVolume = 0.15
    var musicSource = "library"  // "library" (musicFile, nil = none) | "ai"
    var musicFile: String?
    var musicModel = "medium"
    var musicVolume = 0.10  // under narration
    var musicBedVolume = 0.25  // nobody talking
    var brandBanner: String?  // nil = no channel branding
    var brandLogo: String?

    init() {}

    init(_ settings: ProjectSettings) {
        targetRatio = settings.targetRatio ?? targetRatio
        style = settings.narrationStyle.flatMap(NarrationStyle.init(rawValue:)) ?? style
        voice = settings.voice ?? voice
        ratePercent = settings.ttsRate.map(Durations.ratePercent) ?? ratePercent
        duckVolume = settings.duckVolume ?? duckVolume
        musicSource = settings.musicSource ?? musicSource
        musicFile = settings.musicFile
        musicModel = settings.musicModel ?? musicModel
        musicVolume = settings.musicVolume ?? musicVolume
        musicBedVolume = settings.musicBedVolume ?? musicBedVolume
        brandBanner = settings.brandBanner
        brandLogo = settings.brandLogo
    }

    var assignments: [String: String] {
        [
            "target_ratio": String(format: "%.2f", targetRatio),
            "narration_style": style.rawValue,
            "tts_engine": "vieneu",
            "voice": voice,
            "tts_rate": Durations.rateText(percent: ratePercent),
            "duck_volume": String(format: "%.2f", duckVolume),
            "music_source": musicSource,
            "music_file": musicFile ?? "",  // empty = no music
            "music_model": musicModel,
            "music_volume": String(format: "%.2f", musicVolume),
            "music_bed_volume": String(format: "%.2f", musicBedVolume),
            "brand_banner": brandBanner ?? "",  // empty = no branding
            "brand_logo": brandLogo ?? "",
        ]
    }

    /// Only what changed compared to `old`, so unchanged stages are not marked stale.
    func changes(from old: EditableSettings) -> [String: String] {
        let before = old.assignments
        return assignments.filter { before[$0.key] != $0.value }
    }
}

struct NewProjectSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let initialFile: URL?
    let onCreated: (URL) -> Void

    @State private var source: URL?
    @State private var title = ""
    @State private var settings = EditableSettings()
    @State private var runNow = true
    @State private var isCreating = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Dự án mới").font(.title2.bold())
            Form {
                LabeledContent("File phim") {
                    HStack {
                        Text(source?.lastPathComponent ?? "Chưa chọn (kéo thả vào đây)")
                            .foregroundStyle(source == nil ? .secondary : .primary)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Button("Chọn…", action: chooseFile)
                    }
                }
                TextField("Tên", text: $title, prompt: Text("Tên phim (lấy từ tên file)"))
                SettingsFields(settings: $settings)
                Toggle("Chạy toàn bộ pipeline ngay sau khi tạo", isOn: $runNow)
            }
            .formStyle(.grouped)
            if let error { Text(error).foregroundStyle(.red).font(.callout) }
            HStack {
                Spacer()
                Button("Hủy", role: .cancel) { dismiss() }
                Button("Tạo dự án") { Task { await create() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(source == nil || title.trimmingCharacters(in: .whitespaces).isEmpty || isCreating)
            }
        }
        .padding(20)
        .frame(minWidth: 600, idealWidth: 640, minHeight: 520, idealHeight: 780)
        .dropDestination(for: URL.self) { urls, _ in
            guard let file = urls.first else { return false }
            use(file)
            return true
        }
        .onAppear {
            if let initialFile { use(initialFile) }
            if let images = model.settings.brandImages {  // the channel's banner is on by default
                settings.brandBanner = images.banner.path
                settings.brandLogo = images.logo?.path
            }
        }
    }

    private func use(_ file: URL) {
        source = file
        if title.isEmpty { title = file.deletingPathExtension().lastPathComponent }
    }

    private func chooseFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.movie, .video, .mpeg4Movie, .quickTimeMovie]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let file = panel.url { use(file) }
    }

    private func create() async {
        guard let source else { return }
        isCreating = true
        defer { isCreating = false }
        do {
            let dir = try await model.createProject(source: source, title: title, updates: settings.assignments)
            if runNow, let project = model.project(dir) { model.runAll(project) }
            onCreated(dir)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Fields shared by the new-project and project-settings sheets.
struct SettingsFields: View {
    @Binding var settings: EditableSettings

    var body: some View {
        LabeledContent("Độ dài tóm tắt") {
            HStack {
                Slider(value: $settings.targetRatio, in: EditableSettings.ratioRange, step: EditableSettings.ratioStep)
                Text("\(Int((settings.targetRatio * 100).rounded()))% phim").monospacedDigit().frame(width: 70, alignment: .trailing)
            }
        }
        Picker("Phong cách kể", selection: $settings.style) {
            ForEach(NarrationStyle.allCases) { Text($0.label).tag($0) }
        }
        LabeledContent("Âm thanh phim khi có lời kể") {
            HStack {
                Slider(value: $settings.duckVolume, in: 0...0.6, step: 0.05)
                Text("\(Int((settings.duckVolume * 100).rounded()))%").monospacedDigit().frame(width: 44, alignment: .trailing)
            }
        }
        Section("Giọng đọc") {
            VoicePickerView(voice: $settings.voice, ratePercent: $settings.ratePercent)
        }
        Section("Nhạc nền") {
            MusicPickerView(settings: $settings)
        }
        Section("Kênh") {
            BrandingField(settings: $settings)
        }
    }
}

struct ProjectSettingsSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let project: ProjectSummary

    @State private var settings = EditableSettings()
    @State private var original = EditableSettings()
    @State private var isSaving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Cài đặt: \(project.file.title)").font(.title2.bold())
            Form { SettingsFields(settings: $settings) }.formStyle(.grouped)
            if let error { Text(error).foregroundStyle(.red).font(.callout) }
            HStack {
                Text("Đổi giọng hoặc độ dài sẽ đánh dấu các bước liên quan là “Cần chạy lại”.")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Hủy", role: .cancel) { dismiss() }
                Button("Lưu") { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(settings == original || isSaving)
            }
        }
        .padding(20)
        .frame(minWidth: 600, idealWidth: 640, minHeight: 480, idealHeight: 720)
        .onAppear {
            original = EditableSettings(project.file.settings)
            settings = original
        }
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await model.setSettings(project.id, settings.changes(from: original))
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
