import AppKit
import RecapKit
import SwiftUI

/// The project screen's tabs (spec §7.2: pipeline, scene browser, characters, script editor, preview & export).
enum ProjectTab: String, CaseIterable, Identifiable {
    case pipeline, scenes, characters, script, preview

    var id: String { rawValue }

    var label: String {
        switch self {
        case .pipeline: "Các bước"
        case .scenes: "Cảnh"
        case .characters: "Nhân vật"
        case .script: "Kịch bản"
        case .preview: "Xem trước"
        }
    }
}

/// Project screen: one tab at a time above the log, which sits below a splitter.
struct ProjectDetailView: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary
    @State private var tab: ProjectTab
    @State private var showSettings = false
    @State private var showLog = false
    @State private var previewLine: String?  // line to jump to when the preview tab opens

    static let logHeight: CGFloat = 220

    init(project: ProjectSummary, initialTab: ProjectTab = .pipeline) {
        self.project = project
        _tab = State(initialValue: initialTab)
    }

    private var state: ProjectRunState { model.runState(project.id) }
    private var isBusy: Bool { model.isBusy(project.id) }

    var body: some View {
        VStack(spacing: 0) {
            Picker("Màn hình", selection: $tab) {
                ForEach(ProjectTab.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 620)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(.bar)
            .layoutPriority(1)
            Divider()
            Group {
                switch tab {
                case .pipeline: pipeline
                case .scenes: SceneBrowserView(project: project)
                case .characters: CharactersView(project: project)
                case .script:
                    ScriptEditorView(project: project) { line in
                        previewLine = line
                        tab = .preview
                    }
                case .preview: PreviewView(project: project, focusLine: $previewLine)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            LogPanel(state: state, isExpanded: $showLog)
                .frame(height: showLog ? Self.logHeight : nil)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { showLog = state.isActive }
        .onChange(of: state.isActive) { _, active in if active { showLog = true } }
        .sheet(isPresented: $showSettings) { ProjectSettingsSheet(project: project) }
    }

    private var pipeline: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                ProjectHeader(project: project, showSettings: $showSettings)
                if let error = state.lastError, !isBusy {
                    ErrorBanner(error: error, retry: retry(error), fix: fix(error))
                }
                StageList(project: project)
                if project.file.settings.musicSource == "ai" {
                    AIMusicView(project: project)
                }
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private func retry(_ error: StageFailureInfo) -> (() -> Void)? {
        guard error.retryable, let stage = error.stage, error.code != Self.unnamedCode else { return nil }
        return { model.runFrom(stage, project) }
    }

    static let unnamedCode = "characters_unnamed"

    /// The way out of an error that needs the user, not a retry: naming the characters.
    private func fix(_ error: StageFailureInfo) -> ErrorBanner.Fix? {
        guard error.code == Self.unnamedCode else { return nil }
        return ErrorBanner.Fix(title: "Đặt tên nhân vật", systemImage: "person.2") { tab = .characters }
    }
}

struct ProjectHeader: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary
    @Binding var showSettings: Bool

    private var isBusy: Bool { model.queue.isBusy(project: project.id) }
    private var isRunning: Bool { model.queue.isRunning(project: project.id) }

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            Thumbnail(url: project.thumbnail, cornerRadius: 10)
                .frame(width: 208, height: 117)
                .shadow(color: .black.opacity(0.18), radius: 6, y: 3)
            VStack(alignment: .leading, spacing: 10) {
                Text(project.file.title).font(.largeTitle.bold()).lineLimit(2)
                chips
                Text(project.file.sourcePath)
                    .font(.caption).foregroundStyle(.tertiary).lineLimit(1).truncationMode(.middle)
                    .help(project.file.sourcePath)
                buttons
                overallProgress
            }
        }
    }

    private var chips: some View {
        let settings = project.file.settings
        let target = project.file.targetDuration.map { " → \(Durations.clock($0))" } ?? ""
        return HStack(spacing: 6) {
            if let duration = project.file.media?.duration {
                Chip(symbol: "film", text: Durations.clock(duration) + target)
            }
            if let voice = settings.voice {
                Chip(symbol: "waveform", text: "\(voice) \(settings.ttsRate ?? "+0%")")
            }
            if let style = settings.narrationStyle.flatMap(NarrationStyle.init(rawValue:)) {
                Chip(symbol: "theatermasks", text: style.shortLabel)
            }
        }
    }

    private var buttons: some View {
        HStack(spacing: 8) {
            if isBusy {
                Button(role: .destructive) { model.cancel(project) } label: {
                    Label(isRunning ? "Hủy" : "Bỏ khỏi hàng đợi", systemImage: "stop.fill")
                }
                .controlSize(.large)
                if !isRunning, let current = model.queue.current {
                    Text("Đang chờ: \(current.title)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            } else {
                Button { model.runAll(project) } label: { Label("Chạy toàn bộ", systemImage: "play.fill") }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(model.isBusy(project.id))  // a short command is saving this project
                    .help("Chạy các bước chưa xong hoặc cần chạy lại; bước còn hợp lệ được bỏ qua")
            }
            Button { showSettings = true } label: { Label("Cài đặt", systemImage: "slider.horizontal.3") }
                .controlSize(.large)
                .disabled(isBusy)
                .help(isBusy ? "Đợi tác vụ của dự án này xong" : "Giọng đọc, độ dài, phong cách")
            Button { NSWorkspace.shared.open(project.id.appending(path: "output")) } label: {
                Label("Output", systemImage: "folder")
            }
            .controlSize(.large)
            .help("Mở thư mục chứa video và phụ đề")
        }
    }

    private var overallProgress: some View {
        let done = StageName.allCases.filter { project.file.state(of: $0).status == .done }.count
        let total = StageName.allCases.count
        return HStack(spacing: 8) {
            ProgressView(value: Double(done), total: Double(total))
                .frame(maxWidth: 240)
                .tint(done == total ? .green : .accentColor)
            Text("\(done)/\(total) bước xong").font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
    }
}

struct Chip: View {
    let symbol: String
    let text: String

    var body: some View {
        Label(text, systemImage: symbol)
            .font(.callout)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(.quaternary.opacity(0.6), in: Capsule())
    }
}

struct Thumbnail: View {
    let url: URL?
    var cornerRadius: CGFloat = 6

    var body: some View {
        Group {
            if let url, let image = NSImage(contentsOf: url) {
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fill)
            } else {
                Rectangle().fill(.quaternary)
                    .overlay(Image(systemName: "film").font(.title2).foregroundStyle(.secondary))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
    }
}

/// The eight stages as one rounded card (spec §7.2 Pipeline View).
struct StageList: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary

    private var run: ProjectRunState { model.runState(project.id) }
    private var isRunning: Bool { model.queue.isRunning(project: project.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Các bước").font(.title3.bold())
            VStack(spacing: 0) {
                ForEach(Array(StageName.allCases.enumerated()), id: \.element) { index, stage in
                    if index > 0 { Divider().padding(.leading, 56) }
                    StageRow(number: index + 1, stage: stage, status: status(of: stage),
                             state: project.file.state(of: stage), run: run,
                             isBusy: model.isBusy(project.id),
                             actions: StageActions(runFrom: { model.runFrom(stage, project) },
                                                   rerun: { model.rerun(stage, project) }))
                }
            }
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.separator))
        }
    }

    private func status(of stage: StageName) -> StageStatus {
        if run.activeStage == stage { return .running }
        return project.file.displayStatus(of: stage, isRunning: isRunning)
    }
}

struct StageActions {
    let runFrom: () -> Void
    let rerun: () -> Void
}

struct StageRow: View {
    static let reusedBelowSec = 0.5

    let number: Int
    let stage: StageName
    let status: StageStatus
    let state: StageState
    let run: ProjectRunState
    let isBusy: Bool
    let actions: StageActions

    var body: some View {
        HStack(spacing: 14) {
            StepBadge(number: number, status: status)
            VStack(alignment: .leading, spacing: 4) {
                Text(stage.label).font(.body.weight(status == .running ? .semibold : .medium))
                if status == .running {
                    TimelineView(.periodic(from: .now, by: 1)) { context in running(now: context.date) }
                } else {
                    Text(detail).font(.caption).foregroundStyle(status == .failed ? .red : .secondary).lineLimit(2)
                }
            }
            Spacer(minLength: 12)
            if status != .running, status != .pending {
                Text(status.label).font(.caption.weight(.medium)).foregroundStyle(status.color)
            }
            Menu {
                Button("Chạy từ bước này", systemImage: "play", action: actions.runFrom)
                Button("Chạy lại riêng bước này", systemImage: "arrow.clockwise", action: actions.rerun)
            } label: {
                Image(systemName: "ellipsis.circle").font(.title3)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .disabled(isBusy)
            .help(isBusy ? "Đang có tác vụ cho dự án này" : "Chạy bước này")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(status == .running ? Color.accentColor.opacity(0.07) : .clear)
    }

    private func running(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let fraction = run.fraction {
                ProgressView(value: fraction).frame(maxWidth: 420)
            } else {
                ProgressView().progressViewStyle(.linear).frame(maxWidth: 420)
            }
            Text(runningText(now: now)).font(.caption).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
        }
    }

    private func runningText(now: Date) -> String {
        var parts: [String] = []
        if let fraction = run.fraction { parts.append("\(Int((fraction * 100).rounded()))%") }
        if let started = run.stageStartedAt { parts.append("đã chạy \(Durations.clock(now.timeIntervalSince(started)))") }
        if let left = run.remaining(now: now) { parts.append("còn khoảng \(Durations.clock(left))") }
        if !run.progressMessage.isEmpty { parts.append(run.progressMessage) }
        return parts.joined(separator: " · ")
    }

    private var detail: String {
        switch status {
        case .done:
            guard let elapsed = state.elapsedSec else { return "Đã xong" }
            return elapsed < Self.reusedBelowSec ? "Dùng lại kết quả đã có" : "Chạy trong \(Durations.elapsed(elapsed))"
        case .failed: return state.error?.message ?? "Lỗi"
        case .stale: return "Settings đã đổi, cần chạy lại"
        case .interrupted: return "Bị dừng giữa chừng (app đã thoát?). Chạy lại để tiếp tục"
        case .pending: return "Chưa chạy"
        case .running: return ""
        }
    }
}

/// Numbered circle; the number becomes a status symbol once the stage has a result.
struct StepBadge: View {
    let number: Int
    let status: StageStatus

    var body: some View {
        ZStack {
            Circle().fill(fill)
            Circle().strokeBorder(status == .pending ? Color.secondary.opacity(0.4) : .clear, lineWidth: 1.5)
            switch status {
            case .pending:
                Text("\(number)").font(.callout.weight(.semibold)).foregroundStyle(.secondary)
            case .running:
                ProgressView().controlSize(.small).tint(.white)
            default:
                Image(systemName: symbol).font(.callout.weight(.bold)).foregroundStyle(.white)
            }
        }
        .frame(width: 28, height: 28)
    }

    private var fill: Color {
        status == .pending ? .clear : status.color
    }

    private var symbol: String {
        switch status {
        case .done: "checkmark"
        case .failed: "xmark"
        case .stale: "arrow.clockwise"
        case .interrupted: "pause.fill"
        default: "circle"
        }
    }
}

struct ErrorBanner: View {
    struct Fix {
        let title: String
        let systemImage: String
        let action: () -> Void
    }

    let error: StageFailureInfo
    let retry: (() -> Void)?
    var fix: Fix?

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill").font(.title2).foregroundStyle(.red)
            VStack(alignment: .leading, spacing: 3) {
                Text(error.stage.map { "Lỗi ở bước \($0.label)" } ?? "Lỗi").font(.headline)
                Text(error.message).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if let fix {
                Button(fix.title, systemImage: fix.systemImage, action: fix.action).buttonStyle(.borderedProminent)
            }
            if let retry {
                Button("Thử lại", systemImage: "arrow.clockwise", action: retry).buttonStyle(.borderedProminent).tint(.red)
            }
        }
        .padding(14)
        .background(Color.red.opacity(0.09), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.red.opacity(0.35)))
    }
}

/// Collapsible log: a thin bar until a job runs (or the user opens it).
struct LogPanel: View {
    let state: ProjectRunState
    @Binding var isExpanded: Bool
    @State private var filter: StageName?
    @State private var showRaw = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Button { withAnimation(.snappy) { isExpanded.toggle() } } label: {
                    Label("Log", systemImage: isExpanded ? "chevron.down" : "chevron.right").font(.headline)
                }
                .buttonStyle(.plain)
                .help(isExpanded ? "Thu gọn log" : "Mở log")
                if isExpanded {
                    Picker("Bước", selection: $filter) {
                        Text("Tất cả các bước").tag(StageName?.none)
                        ForEach(StageName.allCases) { Text($0.label).tag(StageName?.some($0)) }
                    }
                    .labelsHidden()
                    .frame(width: 200)
                    Toggle("Log thô", isOn: $showRaw).toggleStyle(.checkbox)
                } else if let last = state.logs.last(where: { $0.level != .raw }) {
                    Text(last.message).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Text("\(lines.count) dòng").font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            if !isExpanded {
                EmptyView()
            } else if lines.isEmpty {
                Divider()
                Text("Chưa có log. Bấm “Chạy toàn bộ” để bắt đầu.")
                    .font(.callout).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Divider()
                ScrollViewReader { proxy in
                    List(lines) { line in
                        Text(line.message)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(line.level.color)
                            .textSelection(.enabled)
                            .listRowSeparator(.hidden)
                            .id(line.id)
                    }
                    .listStyle(.plain)
                    .onChange(of: state.logs.last?.id) { _, last in
                        if let last { proxy.scrollTo(last, anchor: .bottom) }
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private var lines: [LogLine] {
        state.logs.filter { (showRaw || $0.level != .raw) && (filter == nil || $0.stage == filter) }
    }
}

extension LogLevel {
    var color: Color {
        switch self {
        case .info: .primary
        case .warning: .orange
        case .error: .red
        case .raw: .secondary
        }
    }
}
