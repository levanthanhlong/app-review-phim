import RecapKit
import SwiftUI

/// "Nhạc AI": the cues of work/music/plan.json, each with its 3 options to hear and pick, a prompt
/// editor and "make new ones". Changes go through `recap music-choose/-edit`; new audio is made by
/// running the music stage again (only what changed).
struct AIMusicView: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary
    @State private var plan: MusicPlan?
    @State private var editing: MusicPlanCue?
    @State private var error: String?
    @State private var needsRender = false

    private var isBusy: Bool { model.queue.isBusy(project: project.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Nhạc AI").font(.title3.bold())
                Spacer()
                if needsRender {
                    Button("Ghép lại video với nhạc đã chọn", systemImage: "play.fill") { model.runFrom(.music, project) }
                        .disabled(isBusy)
                }
            }
            if let error { Text(error).font(.callout).foregroundStyle(.red) }
            if let plan, !plan.cues.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(plan.cues.enumerated()), id: \.element.id) { index, cue in
                        if index > 0 { Divider() }
                        cueRow(cue)
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.separator))
            } else {
                Text("Chưa có nhạc. Bấm “Chạy toàn bộ” hoặc chạy bước “Tạo nhạc nền”.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .task(id: project.file) { plan = MusicPlan.load(from: project.id) }
        .onDisappear { model.stopMusicPreview() }
        .sheet(item: $editing) { cue in
            PromptEditor(cue: cue) { prompt, alt in
                Task { await edit(cue, prompt: prompt, altPrompt: alt, reroll: false) }
            }
        }
    }

    private func cueRow(_ cue: MusicPlanCue) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(cue.id).font(.headline.monospacedDigit())
                Text("\(Durations.clock(cue.outStart))–\(Durations.clock(cue.outEnd))")
                    .font(.callout.monospacedDigit()).foregroundStyle(.secondary)
                Text(cue.mood).font(.callout)
                Spacer()
                Button("Sửa prompt", systemImage: "pencil") { editing = cue }
                Button("Tạo 3 bản khác", systemImage: "arrow.triangle.2.circlepath") {
                    Task { await edit(cue, prompt: nil, altPrompt: nil, reroll: true) }
                }
            }
            .disabled(isBusy)
            HStack(spacing: 10) {
                if cue.options.isEmpty {
                    Text("Đang chờ tạo nhạc").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(cue.options.enumerated()), id: \.element.id) { index, option in
                    optionChip(cue, index: index, option: option)
                }
            }
            Text(cue.prompt).font(.caption).foregroundStyle(.secondary).lineLimit(2).textSelection(.enabled)
        }
        .padding(14)
    }

    private func optionChip(_ cue: MusicPlanCue, index: Int, option: MusicOption) -> some View {
        let file = option.url(in: project.id)
        let isChosen = index == cue.chosen
        let isPlaying = model.playingMusic == file
        return HStack(spacing: 6) {
            Button {
                if !isChosen { Task { await choose(cue, index) } }
            } label: {
                Label("Bản \(option.label)", systemImage: isChosen ? "largecircle.fill.circle" : "circle")
                    .fontWeight(isChosen ? .semibold : .regular)
            }
            .buttonStyle(.borderless)
            .disabled(isBusy)
            Button {
                play(file)
            } label: {
                Image(systemName: isPlaying ? "stop.circle.fill" : "play.circle")
            }
            .buttonStyle(.borderless)
            .help(isPlaying ? "Dừng" : "Nghe thử (\(option.engine), seed \(option.seed))")
        }
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(isChosen ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08), in: Capsule())
    }

    private func play(_ file: URL) {
        do {
            error = nil
            try model.toggleMusicPreview(file)
        } catch {
            self.error = "Không phát được nhạc: \(error.localizedDescription)"
        }
    }

    private func choose(_ cue: MusicPlanCue, _ option: Int) async {
        do {
            error = nil
            _ = try await model.chooseMusic(project.id, cue: cue.id, option: option)
            plan = MusicPlan.load(from: project.id)
            needsRender = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Saves the change, then runs the music stage (it only generates what changed).
    private func edit(_ cue: MusicPlanCue, prompt: String?, altPrompt: String?, reroll: Bool) async {
        do {
            error = nil
            model.stopMusicPreview()
            _ = try await model.editMusic(project.id, cue: cue.id, prompt: prompt, altPrompt: altPrompt, reroll: reroll)
            plan = MusicPlan.load(from: project.id)
            model.run(.music, project)
            needsRender = true
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Edits the two English prompts of one cue (A/B use the main one, C the alternative).
private struct PromptEditor: View {
    @Environment(\.dismiss) private var dismiss
    let cue: MusicPlanCue
    let save: (String, String) -> Void
    @State private var prompt = ""
    @State private var altPrompt = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Prompt nhạc \(cue.id) · \(cue.mood)").font(.title3.bold())
            Text("Tiếng Anh, không tên phim, nhân vật hay nghệ sĩ. Lưu xong sẽ tạo lại các bản dùng prompt đã đổi.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Prompt chính (bản A, B)").font(.headline)
            TextEditor(text: $prompt).font(.body).frame(height: 70).border(.separator)
            Text("Prompt thay thế (bản C)").font(.headline)
            TextEditor(text: $altPrompt).font(.body).frame(height: 70).border(.separator)
            HStack {
                Spacer()
                Button("Hủy", role: .cancel) { dismiss() }
                Button("Lưu và tạo lại") {
                    save(prompt, altPrompt)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 560)
        .onAppear {
            prompt = cue.prompt
            altPrompt = cue.altPrompt
        }
    }
}
