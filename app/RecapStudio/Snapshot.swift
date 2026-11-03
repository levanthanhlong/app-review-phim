#if DEBUG
import AppKit
import RecapKit
import SwiftUI

/// Debug-only: `RECAP_SNAPSHOT_DIR=/path RecapStudio.app/Contents/MacOS/RecapStudio` renders the main
/// screens to PNG files in-process (no Screen Recording permission needed), then quits.
@MainActor
enum Snapshot {
    static let mainSize = CGSize(width: 1280, height: 820)
    static let smallSize = CGSize(width: 1000, height: 640)

    static func runIfRequested(model: AppModel) {
        guard let path = ProcessInfo.processInfo.environment["RECAP_SNAPSHOT_DIR"] else { return }
        let dir = URL(filePath: path, directoryHint: .isDirectory)
        Task {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            model.reload()
            await model.loadVoices()
            let project = model.projects.first
            await render(ContentView(initialSelection: project?.id), size: mainSize, model: model, to: dir.appending(path: "main.png"))
            await render(ContentView(initialSelection: project?.id), size: smallSize, model: model, to: dir.appending(path: "main-small.png"))
            await render(ContentView(initialSelection: nil), size: smallSize, model: model, to: dir.appending(path: "empty.png"))
            await render(NewProjectSheet(initialFile: nil) { _ in }, size: CGSize(width: 640, height: 1180), model: model,
                         to: dir.appending(path: "new-project.png"))
            if let project {
                await render(ProjectSettingsSheet(project: project), size: CGSize(width: 640, height: 1260), model: model,
                             to: dir.appending(path: "project-settings.png"))
            }
            if let project, project.file.settings.musicSource == "ai" {
                await render(AIMusicView(project: project).padding(24), size: CGSize(width: 980, height: 420), model: model,
                             to: dir.appending(path: "ai-music.png"))
            }
            if let project {
                // simulated events: a running stage with progress and logs, then a failed one
                let state = model.runState(project.id)
                let start = Date.now.addingTimeInterval(-95)
                state.begin(jobID: UUID(), title: "Chạy toàn bộ", at: start)
                state.handle(.stageStart(stage: "describe"), at: start)
                for n in 1...14 { state.handle(.log(level: "info", message: "Mô tả B\(String(format: "%04d", n))"), at: start) }
                state.handle(.progress(stage: "describe", done: 14, total: 45, message: "B0014"), at: start)
                await render(ContentView(initialSelection: project.id), size: mainSize, model: model, to: dir.appending(path: "running.png"))
                state.handle(.error(stage: "script", code: "llm_quota", message: "Claude hết hạn mức, thử lại lúc 18:00 03/11", retryable: true))
                state.handle(.exit(code: 1))
                await render(ContentView(initialSelection: project.id), size: smallSize, model: model, to: dir.appending(path: "error.png"))
            }
            await render(SettingsView(), size: CGSize(width: 720, height: 560), model: model, to: dir.appending(path: "settings.png"))
            NSApp.terminate(nil)
        }
    }

    private static func render(_ view: some View, size: CGSize, model: AppModel, to file: URL) async {
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.titled, .resizable],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false  // ARC owns it; the default would over-release on close()
        let host = NSHostingView(rootView: view.environment(model).frame(width: size.width, height: size.height))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try? await Task.sleep(for: .milliseconds(600))  // let .task / async state settle
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        host.cacheDisplay(in: host.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: file)
        window.close()
    }
}
#endif
