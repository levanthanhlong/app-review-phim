import RecapKit
import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: URL?
    @State private var newProject: NewProjectRequest?

    init(initialSelection: URL? = nil) {
        _selection = State(initialValue: initialSelection)
    }

    var body: some View {
        NavigationSplitView {
            ProjectListView(selection: $selection) { file in
                newProject = NewProjectRequest(file: file)
            }
            .navigationSplitViewColumnWidth(min: 260, ideal: 300)
        } detail: {
            if let project = model.project(selection) {
                ProjectDetailView(project: project)
                    .id(project.id)
            } else {
                ContentUnavailableView {
                    Label("Chưa chọn dự án", systemImage: "film.stack")
                } description: {
                    Text("Chọn một dự án bên trái, hoặc kéo thả file phim vào đây để tạo dự án mới.")
                } actions: {
                    Button { newProject = NewProjectRequest(file: nil) } label: { Label("Dự án mới", systemImage: "plus") }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                }
                .dropDestination(for: URL.self) { urls, _ in
                    guard let file = urls.first else { return false }
                    newProject = NewProjectRequest(file: file)
                    return true
                }
            }
        }
        .sheet(item: $newProject) { request in
            NewProjectSheet(initialFile: request.file) { created in selection = created }
        }
    }
}

struct NewProjectRequest: Identifiable {
    let id = UUID()
    let file: URL?
}

struct ProjectListView: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: URL?
    let onNew: (URL?) -> Void

    var body: some View {
        List(model.projects, selection: $selection) { project in
            ProjectRow(project: project, isRunning: model.queue.isRunning(project: project.id))
                .tag(project.id)
        }
        .overlay {
            if model.projects.isEmpty {
                ContentUnavailableView("Chưa có dự án", systemImage: "plus.rectangle.on.folder",
                                       description: Text("Bấm + hoặc kéo thả file phim vào đây."))
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard let file = urls.first else { return false }
            onNew(file)
            return true
        }
        .toolbar {
            ToolbarItem {
                Button { onNew(nil) } label: { Label("Dự án mới", systemImage: "plus") }
                    .help("Tạo dự án mới")
            }
        }
        .navigationTitle("RecapStudio")
    }
}

struct ProjectRow: View {
    let project: ProjectSummary
    let isRunning: Bool

    var body: some View {
        let status = project.file.overallStatus(isRunning: isRunning)
        HStack(spacing: 10) {
            Thumbnail(url: project.thumbnail, cornerRadius: 5)
                .frame(width: 80, height: 45)
            VStack(alignment: .leading, spacing: 2) {
                Text(project.file.title).font(.headline).lineLimit(1)
                Text(durationText).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                Label(status.label, systemImage: status.symbol)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(status.color)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }

    private var durationText: String {
        guard let duration = project.file.media?.duration else { return "Chưa nạp phim" }
        let target = project.file.targetDuration.map { " → \(Durations.clock($0))" } ?? ""
        return Durations.clock(duration) + target
    }
}

struct StatusBadge: View {
    let status: StageStatus
    var compact = false

    var body: some View {
        if compact {
            Image(systemName: status.symbol).foregroundStyle(status.color).help(status.label)
        } else {
            Label(status.label, systemImage: status.symbol).foregroundStyle(status.color)
        }
    }
}

extension StageStatus {
    var symbol: String {
        switch self {
        case .pending: "circle"
        case .running: "arrow.triangle.2.circlepath"
        case .done: "checkmark.circle.fill"
        case .failed: "xmark.octagon.fill"
        case .stale: "exclamationmark.arrow.triangle.2.circlepath"
        case .interrupted: "pause.circle.fill"
        }
    }

    var color: Color {
        switch self {
        case .pending: .secondary
        case .running: .blue
        case .done: .green
        case .failed: .red
        case .stale, .interrupted: .orange
        }
    }

    var label: String {
        switch self {
        case .pending: "Chưa chạy"
        case .running: "Đang chạy"
        case .done: "Xong"
        case .failed: "Lỗi"
        case .stale: "Cần chạy lại"
        case .interrupted: "Bị gián đoạn"
        }
    }
}
