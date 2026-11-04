import Foundation
import Observation
import RecapKit

/// The Script Editor's lines of one project. Kept by `AppModel` (not the view), so unsaved edits
/// survive switching tabs or projects.
@MainActor
@Observable
final class ScriptEditorState {
    /// The script as last loaded or saved; `lines` are compared with it.
    private(set) var document: ScriptDocument?
    var lines: [ScriptLine] = []
    private(set) var loadError: String?
    var message: String?
    private(set) var isSaving = false
    /// A newer script.yaml (e.g. the script stage ran again) that arrived while there were unsaved edits.
    private(set) var newerOnDisk: ScriptDocument?

    var isDirty: Bool { document.map { $0.items != lines } ?? false }

    var saved: [String: ScriptLine] {
        Dictionary((document?.items ?? []).map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    }

    func replace(_ line: ScriptLine) {
        lines = lines.map { $0.id == line.id ? line : $0 }
    }

    func revert() {
        if let document { lines = document.items }
    }

    /// Reads script.yaml again; unsaved edits are never replaced (a newer file is offered instead).
    func reload(_ project: URL, model: AppModel) async {
        let loaded: ScriptDocument
        do {
            loaded = try await model.loadScript(project)
        } catch {
            if document == nil { loadError = error.localizedDescription }
            return
        }
        loadError = nil
        guard isDirty else {  // checked after the await: the user may have typed meanwhile
            document = loaded
            lines = loaded.items
            newerOnDisk = nil
            return
        }
        newerOnDisk = loaded.items == document?.items ? nil : loaded
    }

    /// Drop the unsaved edits and use the newer script.
    func takeNewer() {
        guard let newerOnDisk else { return }
        document = newerOnDisk
        lines = newerOnDisk.items
        self.newerOnDisk = nil
    }

    /// Keep editing on top of the newer script: saving will replace it with these lines.
    func keepMine() {
        guard let newerOnDisk else { return }
        document = newerOnDisk
        self.newerOnDisk = nil
    }

    /// Saves (when there are edits), reloads, then optionally runs `stages` (from, to). The list is
    /// locked while saving, so nothing typed can be lost.
    func save(_ project: ProjectSummary, model: AppModel, then stages: (StageName, StageName)?) async {
        guard newerOnDisk == nil else {
            message = "Kịch bản vừa được viết lại: chọn giữ bản đang sửa hoặc dùng bản mới trước khi lưu."
            return
        }
        isSaving = true
        defer { isSaving = false }
        do {
            if isDirty { _ = try await model.saveScript(project.id, items: lines) }
            let loaded = try await model.loadScript(project.id)
            document = loaded
            lines = loaded.items
            message = nil
            if let stages {
                let title = stages.0 == stages.1 ? "Tạo lại giọng đọc" : "Ghép lại sau khi sửa kịch bản"
                model.run(from: stages.0, to: stages.1, project, title: title)
            }
        } catch {
            message = "Chưa lưu được: \(error.localizedDescription)"
        }
    }
}
