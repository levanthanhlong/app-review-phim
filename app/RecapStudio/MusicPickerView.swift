import AppKit
import RecapKit
import SwiftUI

/// Background music: a track of the music folder (by genre, with ▶︎ preview) or AI-made cues,
/// and the music levels while narrating and with nobody talking.
struct MusicPickerView: View {
    @Environment(AppModel.self) private var model
    @Binding var settings: EditableSettings
    @State private var playError: String?

    static let voiceRange = 0.0...0.5
    static let bedRange = 0.0...1.0
    static let volumeStep = 0.01

    private var hasMusic: Bool { settings.musicSource == "ai" || settings.musicFile != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Nguồn nhạc", selection: $settings.musicSource) {
                Text("Thư viện").tag("library")
                Text("AI tạo nhạc").tag("ai")
            }
            .pickerStyle(.segmented)
            if settings.musicSource == "ai" {
                aiOptions
            } else {
                library
            }
            level("Nhạc khi đang kể", value: $settings.musicVolume, range: Self.voiceRange)
            level("Nhạc khi không có lời kể", value: $settings.musicBedVolume, range: Self.bedRange)
            if let error = playError ?? model.musicError {
                Text(error).font(.caption).foregroundStyle(.red)
            }
        }
        .task { model.loadMusic() }
        .onDisappear { model.stopMusicPreview() }
    }

    private var aiOptions: some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker("Chất lượng", selection: $settings.musicModel) {
                Text("Cao (Stable Audio 3 medium)").tag("medium")
                Text("Nhanh (Stable Audio 3 small)").tag("sm-music")
            }
            Text("Bước “Tạo nhạc nền” chia video theo không khí, mỗi phần tạo 3 bản nhạc không lời. "
                 + "Nghe và chọn ở mục Nhạc AI trong màn dự án.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var library: some View {
        VStack(alignment: .leading, spacing: 8) {
            list
                .frame(height: 220)  // fixed: a list inside a scrolling Form must not grow
            HStack {
                Text("Thêm nhạc: chép file vào thư mục thể loại, rồi bấm Làm mới.")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Mở thư mục nhạc") { NSWorkspace.shared.open(model.settings.musicRootURL) }
                Button("Làm mới") { model.loadMusic() }
            }
        }
    }

    private func level(_ title: String, value: Binding<Double>, range: ClosedRange<Double>) -> some View {
        HStack {
            Text(title)
            Slider(value: value, in: range, step: Self.volumeStep)
            Text("\(Int((value.wrappedValue * 100).rounded()))%").monospacedDigit().frame(width: 44, alignment: .trailing)
        }
        .disabled(!hasMusic)
    }

    private var list: some View {
        List {
            choiceRow(title: "Không dùng nhạc", subtitle: nil, isSelected: settings.musicFile == nil) { settings.musicFile = nil }
            if let musicFile = settings.musicFile, MusicLibrary.track(atPath: musicFile, in: model.musicGenres) == nil {
                // chosen earlier from somewhere else (or the file was moved): keep showing it
                choiceRow(title: URL(filePath: musicFile).lastPathComponent, subtitle: "Ngoài thư mục nhạc",
                          isSelected: true) {}
            }
            ForEach(model.musicGenres) { genre in
                Section(genre.name) {
                    if genre.tracks.isEmpty {
                        Text("Chưa có bài nào").font(.caption).foregroundStyle(.tertiary)
                    }
                    ForEach(genre.tracks) { track in
                        trackRow(track)
                    }
                }
            }
        }
        .listStyle(.bordered)
    }

    private func trackRow(_ track: RecapKit.MusicTrack) -> some View {
        HStack {
            choiceRow(title: track.title, subtitle: nil, isSelected: isSelected(track)) { settings.musicFile = track.id.path }
            Button {
                play(track)
            } label: {
                Image(systemName: model.playingMusic == track.id ? "stop.circle" : "play.circle")
            }
            .buttonStyle(.borderless)
            .help(model.playingMusic == track.id ? "Dừng" : "Nghe thử")
        }
    }

    private func choiceRow(title: String, subtitle: String?, isSelected: Bool, select: @escaping () -> Void) -> some View {
        HStack {
            Image(systemName: isSelected ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(isSelected ? Color.accentColor : .secondary)
            VStack(alignment: .leading) {
                Text(title).fontWeight(isSelected ? .medium : .regular).lineLimit(1)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: select)
    }

    private func isSelected(_ track: RecapKit.MusicTrack) -> Bool {
        guard let musicFile = settings.musicFile else { return false }
        return URL(filePath: musicFile).standardizedFileURL.path == track.id.standardizedFileURL.path
    }

    private func play(_ track: RecapKit.MusicTrack) {
        do {
            playError = nil
            try model.toggleMusicPreview(track.id)
        } catch {
            playError = "Không phát được \(track.title): \(error.localizedDescription)"
        }
    }
}
