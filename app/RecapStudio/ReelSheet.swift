import AppKit
import RecapKit
import SwiftUI

private struct ReelCut: Decodable, Identifiable {
    let id: String
    let time: Double
    let text: String
    let reason: String?
    let cta: String
}

private struct ReelPlan: Decodable {
    let revision: String
    let selected: String
    let warning: String?
    let cuts: [ReelCut]
}

struct ReelSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let project: ProjectSummary

    @State private var plan: ReelPlan?
    @State private var selection = 0
    @State private var edits: [String: String] = [:]
    @State private var controller = PlayerController()
    @State private var activity: String?
    @State private var problem: String?
    @State private var warning: String?
    @State private var exported: URL?
    @State private var exportDetail: String?

    private var cut: ReelCut? {
        guard let cuts = plan?.cuts, cuts.indices.contains(selection) else { return nil }
        return cuts[selection]
    }
    private var isBusy: Bool { activity != nil || model.isBusy(project.id) }
    private var cta: String { cut.map { edits[$0.id] ?? $0.cta } ?? "" }
    private var canRender: Bool { !isBusy && !cta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && cta.count <= 260 }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Tạo Reel").font(.title2.bold())
            Text("Chọn điểm cắt và nghe thử câu dẫn. Reel dọc 9:16 dài khoảng 4–5 phút, tính cả câu dẫn.")
                .foregroundStyle(.secondary)
            if let plan, let cut {
                HStack(alignment: .top, spacing: 20) {
                    endingPlayer
                    editor(plan, cut: cut)
                }
            } else if activity == nil {
                Button("Đề xuất điểm cắt") { loadPlan() }.disabled(isBusy)
            }
            if let warning { Text(warning).font(.callout).foregroundStyle(.secondary) }
            if let problem { Text(problem).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
            if let activity {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(activity).font(.callout)
                    if let progress = model.runState(project.id).fraction {
                        ProgressView(value: progress).frame(width: 100)
                    }
                    Spacer()
                    Button("Hủy") { model.cancel(project) }
                }
            }
            Divider()
            HStack {
                if let exported {
                    Button("Hiện Reel trong Finder", systemImage: "folder") {
                        NSWorkspace.shared.activateFileViewerSelecting([exported])
                    }
                    if let exportDetail { Text(exportDetail).font(.caption).foregroundStyle(.secondary) }
                }
                Spacer()
                Button("Đóng") { dismiss() }.keyboardShortcut(.cancelAction).disabled(activity != nil)
                Button("Xuất Reel", systemImage: "square.and.arrow.up") { render(ending: false) }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canRender)
            }
        }
        .padding(22)
        .frame(width: 700)
        .task { if plan == nil && activity == nil { loadPlan() } }
        .interactiveDismissDisabled(activity != nil)
        .onChange(of: selection) { _, _ in invalidatePreview() }
        .onChange(of: cta) { _, _ in invalidatePreview() }
        .onDisappear { controller.pause() }
    }

    private var endingPlayer: some View {
        RoundedRectangle(cornerRadius: 9).fill(.black.opacity(0.9))
            .overlay {
                if controller.loadedURL != nil {
                    NativeVideoPlayer(player: controller.player)
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "play.rectangle").font(.largeTitle)
                        Text("Xem thử đoạn kết").font(.caption)
                    }
                    .foregroundStyle(.white.opacity(0.75))
                }
            }
            .frame(width: 189, height: 336)
            .clipShape(RoundedRectangle(cornerRadius: 9))
    }

    private func editor(_ plan: ReelPlan, cut: ReelCut) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Điểm cắt", systemImage: "scissors").font(.headline)
                Spacer()
                Text(Durations.clock(cut.time)).font(.headline.monospacedDigit())
            }
            if plan.cuts.count > 1 {
                Slider(value: Binding(get: { Double(selection) }, set: { selection = Int($0) }),
                       in: 0...Double(plan.cuts.count - 1), step: 1) {
                    Text("Điểm cắt")
                }
                .labelsHidden()
            }
            Picker("Mốc", selection: $selection) {
                ForEach(plan.cuts.indices, id: \.self) { index in
                    let option = plan.cuts[index]
                    Text("\(Durations.clock(option.time)) · \(option.id)\(option.reason == nil ? "" : " · Gợi ý AI")").tag(index)
                }
            }
            Text(cut.text).font(.callout).lineLimit(3)
            if let reason = cut.reason {
                Label(reason, systemImage: "sparkles").font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
            HStack {
                Text("Câu dẫn cuối Reel").font(.headline)
                Spacer()
                Button("Gợi ý lại", systemImage: "sparkles") { suggestCTA(cut.id) }
            }
            TextEditor(text: Binding(get: { edits[cut.id] ?? cut.cta }, set: { edits[cut.id] = $0 }))
                .font(.body)
                .scrollContentBackground(.hidden)
                .padding(5)
                .frame(height: 83)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
                .accessibilityLabel("Câu dẫn cuối Reel")
            HStack {
                Button("Xem thử đoạn kết", systemImage: "play.circle") { render(ending: true) }
                    .disabled(!canRender)
                Spacer()
                Text("\(cta.count)/260").font(.caption.monospacedDigit())
                    .foregroundStyle(cta.count > 260 ? Color.red : Color.secondary)
            }
        }
        .disabled(isBusy)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func invalidatePreview() {
        controller.pause()
        controller.load(nil)
    }

    private func loadPlan() {
        submit("reel-plan", options: [], title: "Đang tìm điểm cắt") { data in
            let result = try data.decode(ReelPlan.self)
            guard let index = result.cuts.firstIndex(where: { $0.id == result.selected }) else {
                throw AppError("Worker không trả về điểm cắt hợp lệ.")
            }
            plan = result
            selection = index
            warning = result.warning
        }
    }

    private func suggestCTA(_ id: String) {
        submit("reel-plan", options: ["--cut", id], title: "Đang viết câu dẫn") { data in
            let result = try data.decode(ReelPlan.self)
            guard result.revision == plan?.revision else {
                throw AppError("Bản review đã thay đổi. Hãy mở lại Tạo Reel.")
            }
            warning = result.warning
            if let suggestion = result.cuts.first, suggestion.reason != nil { edits[id] = suggestion.cta }
        }
    }

    private func render(ending: Bool) {
        guard canRender, let plan, let cut else { return }
        controller.pause()
        let options = ["--cut", cut.id, "--cta=\(cta)", "--revision", plan.revision]
        submit(ending ? "reel-preview" : "reel-export", options: options,
               title: ending ? "Đang tạo đoạn xem thử" : "Đang xuất Reel") { data in
            guard let path = data["file"]?.stringValue else { throw AppError("Worker không trả về video Reel.") }
            let url = URL(filePath: path)
            if ending {
                controller.load(url, reload: true)
                controller.seek(to: 0)
            } else {
                exported = url
                let length = Durations.clock(data["duration"]?.doubleValue ?? 0)
                exportDetail = data["source"]?.stringValue == "preview" ? "\(length) · Từ bản xem trước" : length
            }
        }
    }

    private func submit(_ command: String, options: [String], title: String,
                        onResult: @escaping (JSONValue) throws -> Void) {
        guard !isBusy else { return }
        problem = nil
        activity = title
        model.runReel(project, command: command, options: options, title: title) { event in
            switch event {
            case let .result(_, data):
                do { try onResult(data) }
                catch { problem = error.localizedDescription }
            case let .error(_, _, message, _):
                problem = message
            case let .exit(code):
                activity = nil
                if code != 0 && code != 130 && problem == nil { problem = "Tác vụ Reel gặp lỗi. Xem log để biết chi tiết." }
            default:
                break
            }
        }
    }
}
