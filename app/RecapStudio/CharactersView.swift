import RecapKit
import SwiftUI

/// "Nhân vật" tab: the people the `characters` stage found (face pictures) and the names heard in
/// the film; the user names each person (a name from the film, a generic label, or their own),
/// merges two cards that are the same person, or leaves extras out. Saving lets the script call
/// everyone by the right name (the script stage waits for it).
struct CharactersView: View {
    @Environment(AppModel.self) private var model
    let project: ProjectSummary

    @State private var report: CharacterReport?
    @State private var saved = CharacterChoices()
    @State private var draft = CharacterChoices()
    @State private var message: String?
    @State private var isSaving = false
    @State private var showLabels = false
    @State private var sceneCharacter: FilmCharacter?

    private var isBusy: Bool { model.isBusy(project.id) }
    private var hasChanges: Bool { draft != saved }
    private var reloadKey: String { project.file.state(of: .characters).finishedAt ?? "-" }

    var body: some View {
        Group {
            if let report, !report.characters.isEmpty {
                HSplitView {
                    VStack(spacing: 0) {
                        toolbar(report).padding(.horizontal, 14).padding(.vertical, 8)
                        Divider()
                        grid(report)
                    }
                    .frame(minWidth: 480, maxWidth: .infinity)
                    HeardNames(report: report).frame(minWidth: 220, idealWidth: 280, maxWidth: 380)
                }
            } else if report != nil {
                ContentUnavailableView("Không thấy nhân vật nào", systemImage: "person.crop.circle.badge.questionmark",
                                       description: Text("Phim ít cảnh thấy rõ mặt. Kịch bản sẽ tự gọi tên chung."))
            } else {
                ContentUnavailableView("Chưa tìm nhân vật", systemImage: "person.2",
                                       description: Text("Chạy tới bước “Nhân vật” ở tab Các bước."))
            }
        }
        .task(id: reloadKey) { reload() }
        .sheet(isPresented: $showLabels) { CharacterLabelsSheet() }
        .sheet(item: $sceneCharacter) { character in
            CharacterScenesView(character: character, projectDir: project.id, heard: report?.names ?? [],
                                labels: model.settings.characterLabels, choices: $draft)
        }
    }

    private func toolbar(_ report: CharacterReport) -> some View {
        HStack(spacing: 10) {
            statusText
            Spacer()
            if isSaving { ProgressView().controlSize(.small) }
            if report.characters.contains(where: { $0.pictures.contains { $0.hasPrefix("characters/faces/") } }) {
                Button("Cập nhật ảnh", systemImage: "photo") { model.run(.characters, project) }
                    .disabled(hasChanges)
                    .help("Tạo ảnh mới từ dữ liệu khuôn mặt đã lưu. Lưu tên đang sửa trước khi cập nhật.")
            }
            Button("Sửa danh sách tên chung…", systemImage: "list.bullet") { showLabels = true }
            Button("Bỏ qua, để máy tự gọi tên") { save(CharacterChoices.skipPayload()) }
                .help("Kịch bản sẽ gọi nhân vật bằng cách gọi chung (cô gái, người đàn ông…)")
            Button("Lưu tên", systemImage: "checkmark") { saveDraft() }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut("s", modifiers: .command)
        }
        .disabled(isBusy || isSaving)
    }

    @ViewBuilder private var statusText: some View {
        if let message {
            Text(message).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        } else if hasChanges {
            Text("Có thay đổi chưa lưu").font(.caption).foregroundStyle(.orange)
        } else if saved.confirmed {
            Label("Đã lưu", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.green)
        } else {
            Text("Đặt tên rồi bấm “Lưu tên”: bước Viết kịch bản đợi bước này").font(.caption).foregroundStyle(.orange)
        }
    }

    private func grid(_ report: CharacterReport) -> some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 320), spacing: 14)], spacing: 14) {
                ForEach(report.characters) { character in
                    CharacterCard(character: character, others: report.characters.filter { $0.id != character.id },
                                  heard: report.names, labels: model.settings.characterLabels,
                                  projectDir: project.id, choices: $draft) {
                        sceneCharacter = character
                    }
                }
            }
            .padding(14)
        }
        .disabled(isSaving)
    }

    private func reload() {
        report = CharacterReport.load(from: project.id)
        saved = CharacterChoices.load(from: project.id)
        draft = saved
    }

    private func saveDraft() {
        do {
            save(try draft.savePayload())
        } catch {
            message = "Không lưu được: \(error.localizedDescription)"
        }
    }

    private func save(_ payload: Data) {
        isSaving = true
        message = nil
        Task {
            defer { isSaving = false }
            do {
                let stale = try await model.saveNaming(project.id, payload: payload)
                reload()
                message = stale.isEmpty ? "Đã lưu. Chạy tiếp ở tab Các bước để viết kịch bản."
                    : "Đã lưu. Cần chạy lại: \(stale.compactMap { StageName(rawValue: $0)?.label }.joined(separator: ", "))"
            } catch {
                message = error.localizedDescription
            }
        }
    }
}

/// Names said in the dialogue, with how often and a few lines, so the user can tell who is who.
private struct HeardNames: View {
    let report: CharacterReport

    var body: some View {
        List {
            Section("Tên nghe được trong phim") {
                if report.names.isEmpty {
                    Text(report.namesWarning ?? "Không nghe thấy tên riêng nào").font(.callout).foregroundStyle(.secondary)
                }
                ForEach(report.names, id: \.self) { candidate in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(candidate.name).font(.headline)
                            Spacer()
                            Text("\(candidate.count) lần").font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        }
                        ForEach(candidate.examples, id: \.self) { line in
                            Text("“\(line)”").font(.caption).italic().foregroundStyle(.secondary).lineLimit(3)
                        }
                    }
                    .padding(.vertical, 2)
                    .textSelection(.enabled)
                }
            }
            if let warning = report.namesWarning, !report.names.isEmpty {
                Text(warning).font(.caption).foregroundStyle(.orange)
            }
        }
    }
}
