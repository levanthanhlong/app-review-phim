import RecapKit
import SwiftUI

struct ScriptLineActions {
    let setText: (String) -> Void
    let pickClip: () -> Void
    let playTTS: () -> Void
    let preview: () -> Void
    let regenerateTTS: () -> Void
    let rewrite: (String) async throws -> String
    let canSave: Bool
}

/// One script line: id, kind, editable text, footage, length, fit and its buttons.
struct ScriptLineRow: View {
    let line: ScriptLine
    let isEdited: Bool
    let tts: TTSLine?
    let entry: TimelineEntry?
    let speechRate: Double
    let actions: ScriptLineActions

    @State private var showRewrite = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(line.id).font(.caption.weight(.semibold).monospacedDigit())
                Text(line.isNarration ? "Lời kể" : "Thoại gốc")
                    .font(.caption2).padding(.horizontal, 5).padding(.vertical, 1)
                    .background((line.isNarration ? Color.accentColor : Color.purple).opacity(0.15), in: Capsule())
                if isEdited { Text("Đã sửa").font(.caption2).foregroundStyle(.orange) }
            }
            .frame(width: 64, alignment: .leading)
            VStack(alignment: .leading, spacing: 6) {
                content
                footage
            }
            Spacer(minLength: 8)
            buttons
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder private var content: some View {
        if line.isNarration {
            TextField("Lời kể", text: Binding(get: { line.text ?? "" }, set: actions.setText), axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...4)
        } else {
            Text(line.note?.isEmpty == false ? line.note ?? "" : "Giữ nguyên hình và tiếng gốc")
                .foregroundStyle(.secondary)
        }
    }

    private var footage: some View {
        let words = ScriptLine.wordCount(line.text ?? "")
        let seconds = ScriptEstimate.seconds(line, ttsDuration: isEdited ? nil : tts?.duration, wordsPerSec: speechRate)
        return HStack(spacing: 10) {
            Text(clipText).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            if line.isNarration {
                Text("\(words) từ · đọc ~\(String(format: "%.1f", seconds))s / hình \(String(format: "%.1f", line.clipSeconds))s")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(seconds > line.clipSeconds / 0.85 ? .orange : .secondary)
                    .help("Hình ngắn hơn lời kể thì phải kéo dài hoặc làm chậm cảnh")
                Button("Đổi cảnh", action: actions.pickClip).buttonStyle(.link).font(.caption)
            }
            if let entry, !isEdited { FitBadge(fit: entry.fit, isNarration: entry.isNarration) }
        }
    }

    private var clipText: String {
        if let clips = line.clips {
            return clips.map { "\($0.beat) \(Durations.clock($0.start))–\(Durations.clock($0.end))" }.joined(separator: ", ")
        }
        return "\(line.beat ?? "") \(Durations.clock(line.start ?? 0))–\(Durations.clock(line.end ?? 0))"
    }

    private var buttons: some View {
        HStack(spacing: 4) {
            if line.isNarration {
                Button(action: actions.playTTS) { Image(systemName: "speaker.wave.2") }
                    .disabled(tts == nil || isEdited)
                    .help(isEdited ? "Câu đã sửa: tạo lại giọng đọc trước" : "Nghe giọng đọc")
            }
            Button(action: actions.preview) { Image(systemName: "play.rectangle") }
                .disabled(entry == nil)
                .help("Xem đoạn ghép trong bản xem trước")
            if line.isNarration {
                Button(action: actions.regenerateTTS) { Image(systemName: "arrow.clockwise") }
                    .disabled(!isEdited || !actions.canSave)
                    .help("Lưu và tạo lại giọng đọc (chỉ các câu đã sửa)")
                Button { showRewrite = true } label: { Image(systemName: "sparkles") }
                    .help("Nhờ AI viết lại câu này")
                    .popover(isPresented: $showRewrite, arrowEdge: .leading) {
                        RewritePopover(original: line.text ?? "", rewrite: actions.rewrite) { text in
                            actions.setText(text)
                            showRewrite = false
                        }
                    }
            }
        }
        .buttonStyle(.borderless)
    }
}

/// "✨ Nhờ AI viết lại câu này": optional hint, then the suggestion to accept or not.
private struct RewritePopover: View {
    let original: String
    let rewrite: (String) async throws -> String
    let onAccept: (String) -> Void

    @State private var hint = ""
    @State private var suggestion: String?
    @State private var error: String?
    @State private var isWorking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Câu hiện tại").font(.caption).foregroundStyle(.secondary)
            Text(original).fixedSize(horizontal: false, vertical: true)
            TextField("Gợi ý cho AI (tùy chọn), ví dụ: kịch tính hơn", text: $hint)
            if let suggestion {
                Text("AI đề xuất").font(.caption).foregroundStyle(.secondary)
                Text(suggestion).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    .padding(8).background(Color.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 6))
            }
            if let error { Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack {
                if isWorking { ProgressView().controlSize(.small) }
                Spacer()
                Button(suggestion == nil ? "Viết lại" : "Viết bản khác", action: ask).disabled(isWorking)
                if let suggestion {
                    Button("Dùng câu này") { onAccept(suggestion) }.buttonStyle(.borderedProminent)
                }
            }
        }
        .padding(14)
        .frame(width: 420)
    }

    private func ask() {
        isWorking = true
        error = nil
        Task {
            defer { isWorking = false }
            do {
                suggestion = try await rewrite(hint)
            } catch {
                self.error = "AI chưa viết được: \(error.localizedDescription)"
            }
        }
    }
}
