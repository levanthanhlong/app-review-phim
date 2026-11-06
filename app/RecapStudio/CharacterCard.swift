import RecapKit
import SwiftUI

/// One person of the "Nhân vật" tab: face pictures, how much they are on screen, and their naming.
struct CharacterCard: View {
    let character: FilmCharacter
    let others: [FilmCharacter]
    let heard: [NameCandidate]
    let labels: [String]
    let projectDir: URL
    @Binding var choices: CharacterChoices
    let showScenes: () -> Void

    private var id: String { character.id }
    private var mergedInto: String? { choices.merged[id] }
    private var isIgnored: Bool { choices.ignored.contains(id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            pictures
            HStack {
                Text(id).font(.headline)
                Text("\(Durations.clock(character.screenSec)) trên màn hình · \(character.beats.count) cảnh")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            if let target = mergedInto {
                Label("Cùng người với \(target)\(choices.displayName(id).map { " (\($0))" } ?? "")", systemImage: "link")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                CharacterNamingFields(id: id, heard: heard, labels: labels, choices: $choices)
            }
            HStack {
                mergePicker
                Spacer()
                Toggle("Bỏ qua (vai phụ)", isOn: Binding(get: { isIgnored }, set: { choices = choices.ignoring(id, $0) }))
                    .toggleStyle(.checkbox)
                    .help("Kịch bản không gọi tên người này")
            }
        }
        .padding(12)
        .opacity(isIgnored ? 0.5 : 1)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    private var pictures: some View {
        let urls = character.pictureURLs(in: projectDir)
        return Button(action: showScenes) {
            VStack(spacing: 6) {
                Thumbnail(url: urls.first, cornerRadius: 8)
                    .frame(width: 132, height: 176).clipped()
                HStack(spacing: 6) {
                    ForEach(urls.dropFirst(), id: \.self) { url in
                        Thumbnail(url: url, cornerRadius: 6).frame(width: 39, height: 52).clipped()
                    }
                }
                Label("Xem cảnh", systemImage: "play.circle").font(.callout)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Xem cảnh của \(id)")
    }

    private var mergePicker: some View {
        Picker("Gộp vào", selection: Binding(get: { mergedInto }, set: { choices = choices.merging(id, into: $0) })) {
            Text("Người riêng").tag(String?.none)
            ForEach(others) { other in
                Text(other.id + (choices.displayName(other.id).map { " · \($0)" } ?? "")).tag(String?.some(other.id))
            }
        }
        .frame(maxWidth: 200)
        .help("Hai thẻ là cùng một người (ví dụ cùng nhân vật ở góc mặt khác)")
    }
}

struct CharacterNamingFields: View {
    let id: String
    let heard: [NameCandidate]
    let labels: [String]
    @Binding var choices: CharacterChoices

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            nameField
            genderPicker
        }
    }

    private var nameField: some View {
        HStack(spacing: 6) {
            TextField("Tên nhân vật", text: Binding(get: { choices.names[id] ?? "" },
                                                     set: { choices = choices.naming(id, $0) }))
                .textFieldStyle(.roundedBorder)
            Menu("Chọn") {
                if !heard.isEmpty {
                    Section("Tên trong phim") {
                        ForEach(heard, id: \.self) { candidate in
                            Button("\(candidate.name) (\(candidate.count) lần)") { choices = choices.naming(id, candidate.name) }
                        }
                    }
                }
                Section("Gọi chung") {
                    ForEach(labels, id: \.self) { label in
                        Button(label) { choices = choices.naming(id, label) }
                    }
                }
            }
            .fixedSize()
        }
    }

    /// For proper names, so the script says "cô" or "anh" (the VL model often guesses wrong).
    private var genderPicker: some View {
        Picker("Giới tính", selection: Binding(get: { choices.genders[id] }, set: { choices = choices.gendering(id, $0) })) {
            Text("Không rõ").tag(String?.none)
            Text("Nam").tag(String?.some("nam"))
            Text("Nữ").tag(String?.some("nữ"))
        }
        .pickerStyle(.segmented)
        .help("Để kịch bản xưng đúng anh hay cô với tên riêng")
    }

}

/// Edit the generic names offered for everyone (one per line).
struct CharacterLabelsSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tên gọi chung").font(.title2.bold())
            Text("Mỗi dòng một cách gọi (người đàn ông, cô gái, bác sĩ…). Dùng cho mọi dự án.")
                .font(.callout).foregroundStyle(.secondary)
            TextEditor(text: $text).font(.body).frame(minHeight: 320).border(.quaternary)
            HStack {
                Button("Khôi phục mặc định") { text = CharacterLabels.defaults.joined(separator: "\n") }
                Spacer()
                Button("Huỷ", role: .cancel) { dismiss() }
                Button("Lưu") {
                    let lines = text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
                    model.settings.characterLabels = lines.filter { !$0.isEmpty }.reduce(into: []) { if !$0.contains($1) { $0.append($1) } }
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 420)
        .onAppear { text = model.settings.characterLabels.joined(separator: "\n") }
    }
}
