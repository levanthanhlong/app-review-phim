import AppKit
import AVKit
import RecapKit
import SwiftUI

/// Preview & Export (spec §7.2.5): the recap video next to its lines; the line being played is
/// highlighted, clicking a line jumps to it.
struct PreviewView: View {
    enum Version: String, CaseIterable, Identifiable {
        case preview, final

        var id: String { rawValue }
        var label: String { self == .preview ? "Bản xem trước (480p)" : "Bản cuối" }
        var path: String { self == .preview ? "output/recap_preview.mp4" : "output/recap.mp4" }
    }

    static let srtPath = "output/recap.srt"

    @Environment(AppModel.self) private var model
    let project: ProjectSummary
    @Binding var focusLine: String?

    @State private var controller = PlayerController()
    @State private var version = Version.preview
    @State private var timeline: [TimelineEntry] = []
    @State private var texts: [String: String] = [:]
    @State private var scriptError: String?

    private var isBusy: Bool { model.isBusy(project.id) }
    private func url(_ version: Version) -> URL { project.id.appending(path: version.path) }
    private func exists(_ version: Version) -> Bool { FileManager.default.fileExists(atPath: url(version).path) }
    private var srtURL: URL { project.id.appending(path: Self.srtPath) }

    /// Changes whenever assemble or export writes new files.
    private var reloadKey: String {
        [StageName.assemble, .export].map { project.file.state(of: $0).finishedAt ?? "-" }.joined(separator: "|")
    }

    private var currentIndex: Int? { Timeline.index(at: controller.currentTime, in: timeline) }

    var body: some View {
        VStack(spacing: 0) {
            toolbar.padding(.horizontal, 16).padding(.vertical, 10)
            Divider()
            if exists(version) {
                HSplitView {
                    VideoPlayer(player: controller.player)
                        .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
                    lineList.frame(minWidth: 300, idealWidth: 380, maxWidth: 520)
                }
            } else {
                ContentUnavailableView {
                    Label(version == .preview ? "Chưa có bản xem trước" : "Chưa có bản cuối", systemImage: "play.rectangle")
                } description: {
                    Text(version == .preview ? "Chạy tới bước “Ghép bản xem trước” ở tab Các bước."
                                             : "Bấm “Render bản cuối” để xuất video từ file phim gốc.")
                }
            }
        }
        .task(id: reloadKey) { await reload() }
        .onChange(of: version) { _, _ in controller.load(exists(version) ? url(version) : nil) }
        .onChange(of: focusLine) { _, _ in jumpToFocus() }
        .onDisappear { controller.pause() }
    }

    private var toolbar: some View {
        HStack(spacing: 10) {
            Picker("Bản", selection: $version) {
                ForEach(Version.allCases) { Text($0.label).tag($0) }
            }
            .labelsHidden()
            .frame(width: 200)
            Spacer()
            Button { model.run(.export, project) } label: { Label("Render bản cuối (1080p)", systemImage: "film.stack") }
                .disabled(isBusy || project.file.state(of: .assemble).status != .done)
                .help(isBusy ? "Đang có tác vụ cho dự án này" : "Render từ file phim gốc, kèm phụ đề .srt và bản gắn phụ đề")
            Button { NSWorkspace.shared.activateFileViewerSelecting([srtURL]) } label: { Label("File SRT", systemImage: "captions.bubble") }
                .disabled(!FileManager.default.fileExists(atPath: srtURL.path))
                .help("Hiện recap.srt trong Finder (được tạo khi render bản cuối)")
            Button { NSWorkspace.shared.open(project.id.appending(path: "output")) } label: { Label("Thư mục Output", systemImage: "folder") }
        }
    }

    private var lineList: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let scriptError {
                Text(scriptError).font(.caption).foregroundStyle(.orange).padding(8)
            }
            ScrollViewReader { proxy in
                List(Array(timeline.enumerated()), id: \.element.id) { index, entry in
                    PreviewLineRow(entry: entry, text: texts[entry.id], isCurrent: index == currentIndex)
                        .contentShape(Rectangle())
                        .onTapGesture { controller.seek(to: entry.outStart) }
                        .listRowBackground(index == currentIndex ? Color.accentColor.opacity(0.15) : Color.clear)
                        .id(entry.id)
                }
                .listStyle(.plain)
                .onChange(of: currentIndex) { _, index in
                    guard let index, timeline.indices.contains(index) else { return }
                    withAnimation(.snappy) { proxy.scrollTo(timeline[index].id, anchor: .center) }
                }
            }
        }
    }

    private func reload() async {
        timeline = Timeline.load(from: project.id) ?? []
        controller.load(exists(version) ? url(version) : nil, reload: true)
        do {
            let script = try await model.loadScript(project.id)
            texts = Dictionary(script.items.map { ($0.id, $0.text ?? $0.note ?? "") }, uniquingKeysWith: { first, _ in first })
            scriptError = nil
        } catch {
            scriptError = "Không đọc được kịch bản: \(error.localizedDescription)"
        }
        jumpToFocus()
    }

    /// Jumps to the line asked for by the Script Editor (once: the request is cleared either way).
    private func jumpToFocus() {
        guard let line = focusLine else { return }
        focusLine = nil
        guard exists(version), let entry = timeline.first(where: { $0.id == line }) else { return }
        controller.seek(to: entry.outStart)
    }
}

private struct PreviewLineRow: View {
    let entry: TimelineEntry
    let text: String?
    let isCurrent: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(Durations.clock(entry.outStart)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Text(entry.id).font(.caption2).foregroundStyle(.tertiary)
            }
            .frame(width: 54, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                if entry.isNarration {
                    Text(text ?? "").font(isCurrent ? .body.weight(.semibold) : .body)
                } else {
                    Label((text ?? "").isEmpty ? "Giữ thoại gốc" : text ?? "", systemImage: "quote.bubble")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
            FitBadge(fit: entry.fit, isNarration: entry.isNarration)
        }
        .padding(.vertical, 3)
    }
}

/// ✅ / ⚠️ short / ✂️ trimmed (spec §7.2.4).
struct FitBadge: View {
    let fit: LineFit
    let isNarration: Bool

    var body: some View {
        if isNarration {
            Image(systemName: fit.symbol).foregroundStyle(fit.color).help(fit.label)
        }
    }
}

extension LineFit {
    var symbol: String {
        switch self {
        case .ok: "checkmark.circle"
        case .short: "exclamationmark.triangle.fill"
        case .trimmed: "scissors"
        }
    }

    var color: Color {
        switch self {
        case .ok: .green
        case .short: .orange
        case .trimmed: .blue
        }
    }

    var label: String {
        switch self {
        case .ok: "Hình vừa với lời kể"
        case .short: "Hình ngắn hơn lời kể (giữ khung hình cuối)"
        case .trimmed: "Hình dài hơn lời kể, đã cắt bớt"
        }
    }
}
