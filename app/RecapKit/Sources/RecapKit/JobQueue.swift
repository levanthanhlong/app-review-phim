import Foundation
import Observation

public struct JobRequest: Identifiable, Sendable, Equatable {
    public let id: UUID
    public let projectDir: URL
    public let arguments: [String]
    public let title: String

    public init(projectDir: URL, arguments: [String], title: String) {
        self.id = UUID()
        self.projectDir = projectDir
        self.arguments = arguments
        self.title = title
    }
}

/// Runs pipeline jobs one at a time (whisper, the VL model and rendering each need most of the RAM);
/// later requests wait in `pending`. Every request's handler ends with an `.exit` event.
@MainActor
@Observable
public final class JobQueue {
    public private(set) var current: JobRequest?
    public private(set) var pending: [JobRequest] = []

    @ObservationIgnored private let launcher: any WorkerLauncher
    @ObservationIgnored private var handlers: [UUID: @MainActor (WorkerEvent) -> Void] = [:]
    @ObservationIgnored private var running: WorkerJob?

    public init(launcher: any WorkerLauncher) {
        self.launcher = launcher
    }

    public func enqueue(_ request: JobRequest, onEvent: @escaping @MainActor (WorkerEvent) -> Void) {
        handlers[request.id] = onEvent
        pending.append(request)
        startNext()
    }

    /// Running job: SIGTERM (it reports its own exit). Pending job: dropped, reported as exit 130.
    public func cancel(_ id: UUID) {
        if current?.id == id {
            running?.cancel()
        } else if pending.contains(where: { $0.id == id }) {
            pending.removeAll { $0.id == id }
            handlers.removeValue(forKey: id)?(.exit(code: 130))
        }
    }

    public func cancelAll() {
        for request in pending { cancel(request.id) }
        if let current { cancel(current.id) }
    }

    public func isRunning(project: URL) -> Bool {
        current?.projectDir == project
    }

    public func isBusy(project: URL) -> Bool {
        isRunning(project: project) || pending.contains { $0.projectDir == project }
    }

    public var hasWork: Bool { current != nil || !pending.isEmpty }

    private func startNext() {
        guard current == nil, !pending.isEmpty else { return }
        let request = pending.removeFirst()
        current = request
        let handler = handlers[request.id] ?? { _ in }
        do {
            let job = try launcher.start(request.arguments)
            running = job
            Task { [weak self] in
                for await event in job.events { handler(event) }
                self?.finish(request)
            }
        } catch {
            handler(.error(stage: nil, code: "worker_unavailable",
                           message: "Không chạy được worker: \(error.localizedDescription)", retryable: false))
            handler(.exit(code: -1))
            finish(request)
        }
    }

    private func finish(_ request: JobRequest) {
        handlers.removeValue(forKey: request.id)
        running = nil
        current = nil
        startNext()
    }
}
