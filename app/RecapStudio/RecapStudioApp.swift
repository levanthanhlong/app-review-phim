import AppKit
import SwiftUI

@main
struct RecapStudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(delegate.model)
                .frame(minWidth: 960, minHeight: 600)
        }
        Settings {
            SettingsView()
                .environment(delegate.model)
        }
    }
}

/// Owns the model so quitting can ask before stopping a running job (spec §7.3).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static let stopTimeout: Duration = .seconds(15)

    let model = AppModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        #if DEBUG
        Snapshot.runIfRequested(model: model)
        #endif
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard model.queue.hasWork else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Đang có tác vụ chạy"
        alert.informativeText = "Thoát sẽ dừng tác vụ đang chạy (các bước đã xong vẫn được giữ). Thoát không?"
        alert.addButton(withTitle: "Dừng và thoát")
        alert.addButton(withTitle: "Ở lại")
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }

        model.queue.cancelAll()  // SIGTERM: the worker stops the stage cleanly
        Task { @MainActor in
            let deadline = ContinuousClock.now + Self.stopTimeout
            while model.queue.hasWork, ContinuousClock.now < deadline {
                try? await Task.sleep(for: .milliseconds(100))
            }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
