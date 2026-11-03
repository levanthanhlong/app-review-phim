import RecapKit
import SwiftUI

/// Voice list with filters and ▶︎ preview (spec §7.2: chọn giọng + nghe thử), plus the speed slider.
struct VoicePickerView: View {
    @Environment(AppModel.self) private var model
    @Binding var voice: String
    @Binding var ratePercent: Int
    @State private var gender = "all"
    @State private var region = "all"
    @State private var sampleText = ""
    @State private var previewError: String?

    static let rateRange = -30...30
    static let rateStep = 5

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Picker("Giới tính", selection: $gender) {
                    Text("Tất cả").tag("all")
                    Text("Nam").tag("male")
                    Text("Nữ").tag("female")
                }
                Picker("Vùng", selection: $region) {
                    Text("Tất cả").tag("all")
                    ForEach(["Bắc", "Trung", "Nam"], id: \.self) { Text($0).tag($0) }
                }
            }
            .pickerStyle(.segmented)

            list
                .frame(height: 260)  // fixed: a list inside a scrolling Form must not grow

            TextField("Câu đọc thử (để trống: câu mặc định)", text: $sampleText)
            HStack {
                Text("Tốc độ")
                Slider(value: rateBinding, in: Double(Self.rateRange.lowerBound)...Double(Self.rateRange.upperBound),
                       step: Double(Self.rateStep))
                Text(Durations.rateText(percent: ratePercent)).monospacedDigit().frame(width: 44, alignment: .trailing)
            }
            if let previewError {
                Text(previewError).font(.caption).foregroundStyle(.red)
            }
        }
        .task { await model.loadVoices() }
    }

    @ViewBuilder private var list: some View {
        if let error = model.voicesError {
            ContentUnavailableView("Không có danh sách giọng", systemImage: "speaker.slash", description: Text(error))
        } else if model.voices.isEmpty {
            ProgressView("Đang tải danh sách giọng…").frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List(filtered, selection: Binding(get: { voice }, set: { voice = $0 ?? voice })) { item in
                row(item).tag(item.id)
            }
            .listStyle(.bordered)
        }
    }

    private func row(_ item: Voice) -> some View {
        HStack {
            Image(systemName: item.id == voice ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(item.id == voice ? Color.accentColor : .secondary)
            VStack(alignment: .leading) {
                HStack(spacing: 4) {
                    Text(item.label).fontWeight(.medium)
                    if item.featured { Image(systemName: "star.fill").font(.caption2).foregroundStyle(.yellow) }
                }
                Text(item.description).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(String(format: "%.1f từ/s", item.wordsPerSec)).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            Button {
                Task { await preview(item) }
            } label: {
                if model.previewing == item.id {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "play.circle")
                }
            }
            .buttonStyle(.borderless)
            .disabled(model.previewing != nil)
            .help("Nghe thử (lần đầu mất khoảng 10 giây để nạp model)")
        }
        .contentShape(Rectangle())
    }

    private var filtered: [Voice] {
        model.voices.filter { (gender == "all" || $0.gender == gender) && (region == "all" || $0.region == region) }
    }

    private var rateBinding: Binding<Double> {
        Binding(get: { Double(ratePercent) }, set: { ratePercent = Int($0.rounded()) })
    }

    private func preview(_ item: Voice) async {
        do {
            previewError = nil
            try await model.playPreview(item, rate: Durations.rateText(percent: ratePercent), text: sampleText)
        } catch {
            previewError = error.localizedDescription
        }
    }
}
