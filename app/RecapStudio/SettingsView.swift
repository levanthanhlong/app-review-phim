import RecapKit
import SwiftUI

/// Global settings + "Kiểm tra môi trường" (`recap doctor`), spec §7.1.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var checks: [DoctorCheck] = []
    @State private var isChecking = false
    @State private var error: String?

    var body: some View {
        @Bindable var settings = model.settings
        Form {
            Section("Đường dẫn") {
                TextField("Thư mục worker", text: $settings.workerDirectory)
                TextField("Python (venv của worker)", text: $settings.pythonPath)
                TextField("Thư mục dự án", text: $settings.projectsRoot)
                TextField("Thư mục chứa ffmpeg, claude, ollama", text: $settings.toolPath)
                    .help("Thêm vào PATH khi chạy worker, phân cách bằng dấu “:”")
                TextField("Thư mục nhạc nền", text: $settings.musicRoot)
                    .help("Mỗi thư mục con là một thể loại nhạc")
                TextField("Thư mục ảnh kênh", text: $settings.brandFolder)
                    .help("Chứa “Banner dưới video….png” và “Ảnh đại diện….png”")
            }
            Section("Môi trường") {
                HStack {
                    Button("Kiểm tra môi trường") { Task { await runDoctor() } }.disabled(isChecking)
                    if isChecking { ProgressView().controlSize(.small) }
                }
                if let error { Text(error).foregroundStyle(.red) }
                ForEach(checks) { check in
                    HStack(alignment: .top) {
                        Text(check.ok ? "✅" : (check.required ? "❌" : "⚠️"))
                        VStack(alignment: .leading) {
                            Text("\(check.name): \(check.detail)")
                            if !check.ok, !check.hint.isEmpty {
                                Text(check.hint).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 720, height: 560)
        .onChange(of: settings.workerDirectory) { model.settingsChanged() }
        .onChange(of: settings.pythonPath) { model.settingsChanged() }
        .onChange(of: settings.projectsRoot) { model.settingsChanged() }
        .onChange(of: settings.toolPath) { model.settingsChanged() }
        .onChange(of: settings.musicRoot) { model.settingsChanged() }
    }

    private func runDoctor() async {
        isChecking = true
        defer { isChecking = false }
        do {
            error = nil
            checks = try await model.doctor()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
