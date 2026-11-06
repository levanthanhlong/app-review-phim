import RecapKit
import SwiftUI

/// Script Editor (spec §7.2.4): edit narration lines, pick new footage, hear the TTS, ask Claude
/// to rewrite one line. Saving goes through `recap script-save` (validated against the beats);
/// "Lưu & ghép lại" then rebuilds only the changed TTS lines and re-renders the preview.
/// The lines live in `AppModel.scriptEditor(_:)`, so unsaved edits survive leaving the tab.
struct ScriptEditorView: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary
    let onPreview: (String) -> Void  // line id: jump to it in the preview tab

    @State private var manifest: [String: TTSLine] = [:]
    @State private var timeline: [String: TimelineEntry] = [:]
    @State private var beats: [SceneBeat] = []
    @State private var pickingClip: ScriptLine?
    @State private var isRevising = false

    private var editor: ScriptEditorState { model.scriptEditor(project.id) }
    private var isBusy: Bool { model.isBusy(project.id) }

    /// Changes whenever script, tts or assemble write new files.
    private var reloadKey: String {
        [StageName.script, .tts, .assemble].map { project.file.state(of: $0).finishedAt ?? "-" }.joined(separator: "|")
    }

    var body: some View {
        Group {
            if let document = editor.document {
                VStack(spacing: 0) {
                    header(document).padding(.horizontal, 16).padding(.vertical, 10)
                    if let newer = editor.newerOnDisk { NewerScriptBanner(newer: newer, editor: editor) }
                    Divider()
                    lineList(document)
                }
            } else if let error = editor.loadError {
                ContentUnavailableView("Chưa có kịch bản", systemImage: "doc.text",
                                       description: Text("\(error)\nChạy tới bước “Viết kịch bản” ở tab Các bước."))
            } else {
                ProgressView("Đang đọc kịch bản…").frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: reloadKey) { await reload() }
        .sheet(isPresented: $isRevising) {
            ReviseScriptSheet(draft: Bindable(editor).feedbackDraft) { feedback in
                model.reviseScript(project, feedback: feedback)
            }
        }
        .sheet(item: $pickingClip) { line in
            ClipPickerSheet(line: line, beats: beats, marks: BeatMarks.load(from: project.id),
                            autoSkips: SourceInspection.load(from: project.id)?.skipBeats ?? [:], projectDir: project.id) { beat in
                editor.replace(line.withClips(from: beat))
            }
        }
    }

    private func lineList(_ document: ScriptDocument) -> some View {
        let saved = editor.saved
        return List(editor.lines) { line in
            ScriptLineRow(line: line, isEdited: saved[line.id] != line, tts: manifest[line.id],
                          entry: timeline[line.id], speechRate: document.speechRate, actions: actions(for: line))
        }
        .listStyle(.inset(alternatesRowBackgrounds: true))
        .disabled(editor.isSaving)  // what is typed while saving would be replaced by the saved script
    }

    // MARK: - Header

    private func header(_ document: ScriptDocument) -> some View {
        let saved = editor.saved
        let total = editor.lines.reduce(0) { sum, line in
            sum + ScriptEstimate.seconds(line, ttsDuration: ttsDuration(line, saved), wordsPerSec: document.speechRate)
        }
        let target = document.targetDurationSec
        let change = target > 0 ? (total / target - 1) * 100 : 0
        let fits = editor.lines.compactMap { saved[$0.id] == $0 ? timeline[$0.id]?.fit : nil }
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(editor.lines.count) dòng · ước tính \(Durations.clock(total)) / mục tiêu \(Durations.clock(target))"
                     + (target > 0 ? String(format: " (%+.0f%%)", change) : ""))
                    .font(.callout).monospacedDigit()
                Text("Hình: ✅ \(fits.filter { $0 == .ok }.count) · ⚠️ \(fits.filter { $0 == .short }.count) ngắn · "
                     + "✂️ \(fits.filter { $0 == .trimmed }.count) cắt bớt")
                    .font(.caption).foregroundStyle(.secondary)
                if let message = editor.message {
                    Text(message).font(.caption).foregroundStyle(.orange).lineLimit(3)
                }
            }
            Spacer()
            if editor.isSaving { ProgressView().controlSize(.small) }
            Button("Nhận xét & viết lại", systemImage: "text.bubble") { isRevising = true }
                .disabled(editor.isDirty || editor.isSaving || isBusy)
                .help(editor.isDirty ? "Lưu các chỗ đang sửa trước, Model AI sẽ viết lại từ bản đã lưu"
                      : isBusy ? "Đợi tác vụ của dự án này xong" : "Viết nhận xét, AI viết lại cả kịch bản theo đó")
            Button("Hoàn tác", systemImage: "arrow.uturn.backward") { editor.revert() }
                .disabled(!editor.isDirty || editor.isSaving)
            Button("Lưu", systemImage: "square.and.arrow.down") { save(then: nil) }
                .disabled(!editor.isDirty || editor.isSaving || isBusy)
            Button { save(then: (.tts, .assemble)) } label: { Label("Lưu & ghép lại", systemImage: "play.rectangle.on.rectangle") }
                .buttonStyle(.borderedProminent)
                .disabled(editor.isSaving || isBusy || (!editor.isDirty && !needsRebuild))
                .help(isBusy ? "Đợi tác vụ của dự án này xong" : "Lưu, tạo giọng đọc cho các câu đã sửa rồi ghép lại bản xem trước")
        }
    }

    private var needsRebuild: Bool {
        [StageName.tts, .assemble].contains { project.file.state(of: $0).status != .done }
    }

    private func ttsDuration(_ line: ScriptLine, _ saved: [String: ScriptLine]) -> Double? {
        saved[line.id]?.text == line.text ? manifest[line.id]?.duration : nil
    }

    // MARK: - Row actions

    private func actions(for line: ScriptLine) -> ScriptLineActions {
        ScriptLineActions(
            setText: { text in editor.replace(line.withText(text)) },
            pickClip: { pickingClip = line },
            playTTS: {
                guard let tts = manifest[line.id] else { return }
                do {
                    try model.playNarration(tts.url(in: project.id))
                } catch {
                    editor.message = "Không phát được giọng đọc: \(error.localizedDescription)"
                }
            },
            preview: { onPreview(line.id) },
            regenerateTTS: { save(then: (.tts, .tts)) },
            rewrite: { hint in try await model.rewriteLine(project.id, line: line.id, hint: hint) },
            canSave: !isBusy && !editor.isSaving
        )
    }

    private func save(then stages: (StageName, StageName)?) {
        Task { await editor.save(project, model: model, then: stages) }
    }

    private func reload() async {
        manifest = TTSManifest.load(from: project.id)
        timeline = Dictionary((Timeline.load(from: project.id) ?? []).map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        beats = SceneBeats.load(from: project.id)
        await editor.reload(project.id, model: model)
    }
}

/// script.yaml changed (e.g. the script stage ran again) while there were unsaved edits.
private struct NewerScriptBanner: View {
    let newer: ScriptDocument
    let editor: ScriptEditorState

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text("Kịch bản vừa được viết lại (\(newer.items.count) dòng) trong khi bạn đang sửa.")
                .font(.callout)
            Spacer()
            Button("Dùng bản mới (bỏ phần đang sửa)") { editor.takeNewer() }
            Button("Giữ bản đang sửa") { editor.keepMine() }
                .help("Khi lưu, bản đang sửa sẽ thay cho kịch bản mới")
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(Color.orange.opacity(0.1))
    }
}

/// Pick the beat a narration line should show (the whole beat; trimmed to fit when assembled).
private struct ClipPickerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let line: ScriptLine
    let beats: [SceneBeat]
    let marks: [String: BeatMark]
    let autoSkips: [String: String]
    let projectDir: URL
    let onPick: (SceneBeat) -> Void
    @State private var selected: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Chọn cảnh cho \(line.id)").font(.title3.bold())
            Text(line.text ?? "").foregroundStyle(.secondary).lineLimit(2)
            BeatGrid(beats: beats.filter { !BeatMarks.isSkipped($0.id, marks: marks, autoSkips: autoSkips) }, marks: marks,
                     projectDir: projectDir, selected: $selected)
                .frame(minWidth: 760, minHeight: 460)
            HStack {
                Spacer()
                Button("Hủy", role: .cancel) { dismiss() }
                Button("Dùng cảnh này") {
                    if let beat = beats.first(where: { $0.id == selected }) { onPick(beat) }
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .disabled(selected == nil)
            }
        }
        .padding(20)
        .onAppear { selected = line.beatIDs.first }
    }
}
