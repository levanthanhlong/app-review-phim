import SwiftUI

/// Notes on the whole script; Claude then rewrites it following them (`recap script-revise`).
struct ReviseScriptSheet: View {
    static let examples = [
        "Câu mở đầu chưa đủ gây tò mò, hãy mở bằng một câu hỏi.",
        "Giọng kể còn khô, thêm nhận xét hài hước kiểu kênh review.",
        "Gọi nhân vật nữ là Pan, không gọi “cô gái”.",
        "Đoạn giữa kể lan man, rút gọn và tập trung vào chuyện tình cảm.",
    ]

    @Environment(\.dismiss) private var dismiss
    @Binding var draft: String
    let onSubmit: (String) -> Void

    private var notes: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Nhận xét kịch bản").font(.title3.bold())
            Text("Viết những điểm muốn sửa. Claude sẽ viết lại cả kịch bản theo nhận xét, giữ các câu đã tốt "
                 + "và vẫn theo đúng các cảnh có trong phim.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextEditor(text: $draft)
                .font(.body)
                .frame(minHeight: 160)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            VStack(alignment: .leading, spacing: 4) {
                Text("Ví dụ (bấm để thêm):").font(.caption).foregroundStyle(.secondary)
                ForEach(Self.examples, id: \.self) { example in
                    Button(example) { draft = notes.isEmpty ? example : notes + "\n" + example }
                        .buttonStyle(.link).font(.caption)
                }
            }
            Text("Kịch bản hiện tại được lưu lại ở work/script_backups. Sau khi viết lại, bấm “Lưu & ghép lại” "
                 + "hoặc chạy từ bước Tạo giọng đọc để có video mới.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Hủy", role: .cancel) { dismiss() }
                Button("Viết lại kịch bản") {
                    onSubmit(notes)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(notes.isEmpty)
            }
        }
        .padding(20)
        .frame(width: 560)
    }
}
