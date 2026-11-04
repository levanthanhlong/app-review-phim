import AVKit
import RecapKit
import SwiftUI

/// Scene Browser (spec §7.2.3): keyframe grid of the beats; a click plays the beat; beats can be
/// marked ⭐ important (the script includes them) or 🚫 skip (the script leaves them out).
/// Beats the `inspect` stage flagged (ads, credits, outtakes...) are skipped unless the user keeps them.
struct SceneBrowserView: View {
    enum Filter: String, CaseIterable, Identifiable {
        case all, star, skip

        var id: String { rawValue }
        var label: String {
            switch self {
            case .all: "Tất cả"
            case .star: "⭐ Quan trọng"
            case .skip: "🚫 Bỏ qua"
            }
        }
    }

    static let proxyPath = "work/proxy_480p.mp4"

    @Environment(AppModel.self) private var model
    let project: ProjectSummary

    @State private var beats: [SceneBeat] = []
    @State private var marks: [String: BeatMark] = [:]
    @State private var autoSkips: [String: String] = [:]
    @State private var selected: String?
    @State private var filter = Filter.all
    @State private var controller = PlayerController()
    @State private var message: String?
    @State private var isMarking = false

    private var isBusy: Bool { model.isBusy(project.id) }

    private var reloadKey: String {
        [StageName.scenes, .inspect, .describe].map { project.file.state(of: $0).finishedAt ?? "-" }.joined(separator: "|")
    }

    private var shown: [SceneBeat] {
        switch filter {
        case .all: beats
        case .star: beats.filter { marks[$0.id] == .star }
        case .skip: beats.filter { isSkipped($0.id) }
        }
    }

    private var selectedBeat: SceneBeat? { beats.first { $0.id == selected } }

    private func isSkipped(_ beat: String) -> Bool {
        BeatMarks.isSkipped(beat, marks: marks, autoSkips: autoSkips)
    }

    var body: some View {
        Group {
            if beats.isEmpty {
                ContentUnavailableView("Chưa có cảnh", systemImage: "square.grid.3x3",
                                       description: Text("Chạy tới bước “Chia cảnh” (và “Mô tả cảnh”) ở tab Các bước."))
            } else {
                HSplitView {
                    VStack(spacing: 0) {
                        toolbar.padding(.horizontal, 14).padding(.vertical, 8)
                        Divider()
                        BeatGrid(beats: shown, marks: marks, autoSkips: autoSkips, projectDir: project.id, selected: $selected)
                    }
                    .frame(minWidth: 420, maxWidth: .infinity)
                    detail.frame(minWidth: 320, idealWidth: 420, maxWidth: 560)
                }
            }
        }
        .task(id: reloadKey) { reload() }
        .onChange(of: selected) { _, _ in playSelected() }
        .onDisappear { controller.pause() }
    }

    private var toolbar: some View {
        HStack {
            Picker("Lọc", selection: $filter) {
                ForEach(Filter.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 340)
            Spacer()
            let starred = marks.values.filter { $0 == .star }.count
            let skipped = beats.filter { isSkipped($0.id) }.count
            Text("\(beats.count) cảnh · ⭐ \(starred) · 🚫 \(skipped)").font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
    }

    @ViewBuilder private var detail: some View {
        if let beat = selectedBeat {
            VStack(alignment: .leading, spacing: 12) {
                VideoPlayer(player: controller.player).frame(minHeight: 220).aspectRatio(16 / 9, contentMode: .fit)
                HStack {
                    Text(beat.id).font(.headline)
                    Text("\(Durations.clock(beat.start)) – \(Durations.clock(beat.end))").monospacedDigit().foregroundStyle(.secondary)
                    Spacer()
                    Button("Phát lại", systemImage: "play.fill") { playSelected() }
                }
                if let desc = beat.desc { Text(desc) }
                if !beat.dialogue.isEmpty {
                    Text("“\(beat.dialogue)”").italic().foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let reason = autoSkips[beat.id] { autoSkipNote(beat, reason: reason) }
                markButtons(beat)
                if let message { Text(message).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                Spacer()
            }
            .padding(16)
        } else {
            ContentUnavailableView("Chọn một cảnh", systemImage: "hand.tap", description: Text("Bấm vào một cảnh để xem đoạn phim đó."))
        }
    }

    private func markButtons(_ beat: SceneBeat) -> some View {
        let current = marks[beat.id]
        return HStack {
            Toggle("⭐ Quan trọng", isOn: markBinding(beat, .star, current)).toggleStyle(.button)
            Toggle("🚫 Bỏ qua", isOn: markBinding(beat, .skip, current)).toggleStyle(.button)
            if isMarking { ProgressView().controlSize(.small) }
        }
        .disabled(isBusy || isMarking)
        .help(isBusy ? "Đợi tác vụ của dự án này xong" : "Kịch bản sẽ được viết lại theo đánh dấu ở lần chạy sau")
    }

    /// Why `inspect` skips this beat, and the way to use it anyway (a "keep" mark).
    private func autoSkipNote(_ beat: SceneBeat, reason: String) -> some View {
        let kept = marks[beat.id] == .keep
        return VStack(alignment: .leading, spacing: 6) {
            Label(kept ? "Đang dùng dù bị tự động bỏ: \(reason)" : "Tự động bỏ: \(reason)",
                  systemImage: kept ? "checkmark.circle" : "eye.slash")
                .font(.callout).foregroundStyle(kept ? Color.secondary : Color.orange)
                .fixedSize(horizontal: false, vertical: true)
            Toggle("Vẫn dùng cảnh này", isOn: markBinding(beat, .keep, marks[beat.id])).toggleStyle(.button)
                .disabled(isBusy || isMarking)
        }
    }

    private func markBinding(_ beat: SceneBeat, _ mark: BeatMark, _ current: BeatMark?) -> Binding<Bool> {
        Binding(get: { current == mark }, set: { on in setMark(beat, on ? mark : nil) })
    }

    private func setMark(_ beat: SceneBeat, _ mark: BeatMark?) {
        isMarking = true
        Task {
            defer { isMarking = false }
            do {
                let stale = try await model.markBeat(project.id, beat: beat.id, mark: mark)
                marks = BeatMarks.load(from: project.id)
                message = stale.contains(StageName.script.rawValue)
                    ? "Đã lưu. Bước “Viết kịch bản” cần chạy lại để dùng đánh dấu mới: Claude sẽ viết kịch bản mới "
                      + "thay cho bản hiện tại (bản cũ, kể cả phần đã sửa tay, được lưu ở work/script_backups)."
                    : "Đã lưu."
            } catch {
                message = "Không lưu được đánh dấu: \(error.localizedDescription)"
            }
        }
    }

    private func reload() {
        beats = SceneBeats.load(from: project.id)
        marks = BeatMarks.load(from: project.id)
        autoSkips = SourceInspection.load(from: project.id)?.skipBeats ?? [:]
        controller.load(project.id.appending(path: Self.proxyPath))
    }

    private func playSelected() {
        guard let beat = selectedBeat else { return }
        controller.play(from: beat.start, to: beat.end)
    }
}

/// Keyframe cards of beats; also used to pick a new clip for a script line.
struct BeatGrid: View {
    static let cardWidth: CGFloat = 200

    let beats: [SceneBeat]
    let marks: [String: BeatMark]
    var autoSkips: [String: String] = [:]
    let projectDir: URL
    @Binding var selected: String?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: Self.cardWidth), spacing: 12)], spacing: 12) {
                    ForEach(beats) { beat in
                        BeatCard(beat: beat, mark: marks[beat.id], isAutoSkipped: autoSkips[beat.id] != nil, projectDir: projectDir,
                                 isSelected: beat.id == selected)
                            .onTapGesture { selected = beat.id }
                            .id(beat.id)
                    }
                }
                .padding(12)
            }
            .onAppear { if let selected { proxy.scrollTo(selected, anchor: .center) } }
        }
    }
}

private struct BeatCard: View {
    let beat: SceneBeat
    let mark: BeatMark?
    let isAutoSkipped: Bool
    let projectDir: URL
    let isSelected: Bool

    private var isSkipped: Bool { mark == .skip || (mark == nil && isAutoSkipped) }

    private var badge: String? {
        switch mark {
        case .star: "⭐"
        case .skip: "🚫"
        case .keep: "✅"
        case nil: isAutoSkipped ? "🤖🚫" : nil
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            AsyncImage(url: beat.keyframeURL(in: projectDir)) { image in
                image.resizable().aspectRatio(16 / 9, contentMode: .fill)
            } placeholder: {
                Rectangle().fill(.quaternary)
            }
            .frame(height: BeatGrid.cardWidth * 9 / 16)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay(alignment: .topTrailing) {
                if let badge { Text(badge).padding(4) }
            }
            .opacity(isSkipped ? 0.45 : 1)
            HStack {
                Text(beat.id).font(.caption.weight(.semibold))
                Spacer()
                Text(Durations.clock(beat.start)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            Text(beat.desc ?? beat.dialogue).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
        .padding(6)
        .background(isSelected ? Color.accentColor.opacity(0.18) : .clear, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(isSelected ? Color.accentColor : .clear, lineWidth: 2))
        .contentShape(Rectangle())
    }
}
