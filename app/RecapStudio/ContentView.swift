import AppKit
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

    @State private var renaming: ProjectSummary?
    @State private var newTitle = ""
    @State private var deleting: ProjectSummary?
    @State private var error: String?

    var body: some View {
        List(model.projects, selection: $selection) { project in
            ProjectRow(project: project, isRunning: model.queue.isRunning(project: project.id))
                .tag(project.id)
                .contextMenu { menu(project) }
        }
        .alert("Đổi tên dự án", isPresented: isPresent($renaming)) {
            TextField("Tên dự án", text: $newTitle)
            Button("Đổi tên") { if let project = renaming { rename(project) } }
            Button("Huỷ", role: .cancel) {}
        } message: {
            Text("Thư mục dự án cũng được đổi theo tên mới. Các bước đã chạy được giữ nguyên.")
        }
        .confirmationDialog("Xoá dự án “\(deleting?.file.title ?? "")”?", isPresented: isPresent($deleting)) {
            Button("Chuyển vào Thùng rác", role: .destructive) { if let project = deleting { trash(project) } }
            Button("Huỷ", role: .cancel) {}
        } message: {
            Text("Cả thư mục dự án (kịch bản, giọng đọc, video đã xuất) vào Thùng rác, lấy lại được từ đó. File phim gốc không bị xoá.")
        }
        .alert("Không làm được", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK") {}
        } message: {
            Text(error ?? "")
        }
        .onDeleteCommand { if let project = model.project(selection) { deleting = project } }
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

    @ViewBuilder private func menu(_ project: ProjectSummary) -> some View {
        let isBusy = model.isBusy(project.id)
        Button("Đổi tên…") {
            newTitle = project.file.title
            renaming = project
        }
        .disabled(isBusy)
        Button("Hiện trong Finder") { NSWorkspace.shared.activateFileViewerSelecting([project.id]) }
        Divider()
        Button("Chuyển vào Thùng rác…", role: .destructive) { deleting = project }
            .disabled(isBusy)
        if isBusy { Text("Đang có tác vụ: đợi xong mới đổi tên hay xoá được") }
    }

    private func isPresent(_ item: Binding<ProjectSummary?>) -> Binding<Bool> {
        Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } })
    }

    private func rename(_ project: ProjectSummary) {
        let title = newTitle
        let wasSelected = selection == project.id
        Task {
            do {
                let renamed = try await model.renameProject(project.id, title: title)
                if wasSelected { selection = renamed }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func trash(_ project: ProjectSummary) {
        do {
            try model.trashProject(project.id)
            if selection == project.id { selection = nil }
        } catch {
            self.error = error.localizedDescription
        }
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
