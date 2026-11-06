import RecapKit
import SwiftUI

/// Play the scenes for one character while editing the same naming draft as the card.
struct CharacterScenesView: View {
    let character: FilmCharacter
    let projectDir: URL
    let heard: [NameCandidate]
    let labels: [String]
    @Binding var choices: CharacterChoices

    @Environment(\.dismiss) private var dismiss
    @State private var controller = PlayerController()
    @State private var beats: [SceneBeat] = []
    @State private var selectedID: String?

    private var proxy: URL { projectDir.appending(path: SceneBrowserView.proxyPath) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(choices.displayName(character.id).map { "\(character.id) · \($0)" } ?? character.id)
                    .font(.title2.bold())
                Spacer()
                Button("Đóng") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            if choices.merged[character.id] == nil {
                CharacterNamingFields(id: character.id, heard: heard, labels: labels, choices: $choices)
                Text("Bấm “Lưu tên” trong tab Nhân vật để lưu thay đổi.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if beats.isEmpty || controller.loadedURL == nil {
                ContentUnavailableView("Chưa có cảnh để phát", systemImage: "film",
                                       description: Text("Cần dữ liệu cảnh và bản video xem trước của phim gốc."))
            } else {
                HStack(alignment: .top, spacing: 12) {
                    NativeVideoPlayer(player: controller.player).frame(width: 400, height: 225)
                    List(beats) { beat in
                        Button { play(beat) } label: {
                            HStack(alignment: .top, spacing: 8) {
                                Thumbnail(url: beat.keyframeURL(in: projectDir)).frame(width: 64, height: 36).clipped()
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("\(Durations.clock(beat.start)) – \(Durations.clock(beat.end))")
                                        .font(.caption.monospacedDigit())
                                    if let description = beat.desc, !description.isEmpty {
                                        Text(description).lineLimit(2)
                                    }
                                    if !beat.dialogue.isEmpty {
                                        Text(beat.dialogue).foregroundStyle(.secondary).lineLimit(2)
                                    }
                                }
                                Spacer(minLength: 0)
                                if selectedID == beat.id {
                                    Image(systemName: "play.fill").foregroundStyle(Color.accentColor)
                                }
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    .frame(height: 285)
                }
            }
        }
        .padding(20)
        .frame(width: 740)
        .onAppear {
            let ids = Set(character.beats)
            beats = SceneBeats.load(from: projectDir).filter { ids.contains($0.id) }
            if let first = beats.first, FileManager.default.fileExists(atPath: proxy.path) {
                controller.load(proxy)
                play(first)
            }
        }
        .onDisappear { controller.pause() }
    }

    private func play(_ beat: SceneBeat) {
        selectedID = beat.id
        controller.play(from: beat.start, to: beat.end)
    }
}
