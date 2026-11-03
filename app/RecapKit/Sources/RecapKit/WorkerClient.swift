import Foundation

/// How to launch the worker: `<executable> <baseArguments...> <command arguments...>`.
public struct WorkerConfig: Sendable, Equatable {
    public let executable: URL
    public let baseArguments: [String]
    public let workingDirectory: URL
    /// Prepended to PATH: apps started from Finder do not get the shell's PATH (ffmpeg, claude, ollama).
    public let extraPath: [String]

    public init(executable: URL, baseArguments: [String], workingDirectory: URL, extraPath: [String]) {
        self.executable = executable
        self.baseArguments = baseArguments
        self.workingDirectory = workingDirectory
        self.extraPath = extraPath
    }

    /// The real worker: `<worker>/.venv/bin/python -m recap`.
    public static func python(workerDirectory: URL, extraPath: [String]) -> WorkerConfig {
        WorkerConfig(
            executable: workerDirectory.appending(path: ".venv/bin/python"),
            baseArguments: ["-m", "recap"],
            workingDirectory: workerDirectory,
            extraPath: extraPath
        )
    }

    func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let inherited = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = (extraPath + [inherited]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        return env
    }
}

public struct WorkerFailure: Error, Sendable, Equatable {
    public let code: String
    public let message: String
    public let retryable: Bool
}

/// Outcome of a short command (`new`, `set`, `voices`, `voice-preview`, `doctor`).
public struct CommandOutcome: Sendable {
    public let result: JSONValue?
    public let failure: WorkerFailure?
    public let exitCode: Int32
    public let events: [WorkerEvent]
}

/// A running worker process. `events` ends with `.exit(code:)`.
public final class WorkerJob: Sendable {
    public let events: AsyncStream<WorkerEvent>
    private let process: Process

    init(process: Process, events: AsyncStream<WorkerEvent>) {
        self.process = process
        self.events = events
    }

    /// SIGTERM: the worker stops the stage cleanly (spec §6) and exits with 130.
    public func cancel() {
        if process.isRunning { process.terminate() }
    }
}

public protocol WorkerLauncher: Sendable {
    func start(_ arguments: [String]) throws -> WorkerJob
}

public struct WorkerClient: WorkerLauncher {
    public let config: WorkerConfig

    public init(config: WorkerConfig) {
        self.config = config
    }

    public func start(_ arguments: [String]) throws -> WorkerJob {
        let process = Process()
        process.executableURL = config.executable
        process.arguments = config.baseArguments + arguments
        process.currentDirectoryURL = config.workingDirectory
        process.environment = config.environment()
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        process.standardInput = FileHandle.nullDevice

        let (stream, continuation) = AsyncStream<WorkerEvent>.makeStream()
        // terminationHandler instead of waitUntilExit(): the latter needs a run loop, which
        // Swift Concurrency's pool threads do not have (it can hang).
        let (exitCodes, exitContinuation) = AsyncStream<Int32>.makeStream()
        process.terminationHandler = { finished in
            exitContinuation.yield(finished.terminationStatus)
            exitContinuation.finish()
        }
        try process.run()
        Task.detached {
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await Self.forward(stdout, continuation) { WorkerEvent.decode(line: $0) } }
                group.addTask { await Self.forward(stderr, continuation) { .raw($0) } }
            }
            for await code in exitCodes {
                continuation.yield(.exit(code: code))
            }
            continuation.finish()
        }
        return WorkerJob(process: process, events: stream)
    }

    public func run(_ arguments: [String]) async throws -> CommandOutcome {
        let job = try start(arguments)
        var events: [WorkerEvent] = []
        var result: JSONValue?
        var failure: WorkerFailure?
        var exitCode: Int32 = -1
        for await event in job.events {
            events.append(event)
            switch event {
            case let .result(_, data): result = data
            case let .error(_, code, message, retryable): failure = WorkerFailure(code: code, message: message, retryable: retryable)
            case let .exit(code): exitCode = code
            default: break
            }
        }
        return CommandOutcome(result: result, failure: failure, exitCode: exitCode, events: events)
    }

    private static func forward(
        _ pipe: Pipe, _ continuation: AsyncStream<WorkerEvent>.Continuation, _ transform: (String) -> WorkerEvent
    ) async {
        for await line in LineReader.lines(of: pipe.fileHandleForReading) {
            continuation.yield(transform(line))
        }
    }
}

/// Splits a pipe's output into UTF-8 lines. Uses `readabilityHandler` rather than
/// `FileHandle.bytes.lines`, which could miss EOF when several workers ran at once.
final class LineReader: @unchecked Sendable {
    private var buffer = Data()
    private let lock = NSLock()

    static func lines(of handle: FileHandle) -> AsyncStream<String> {
        let reader = LineReader()
        return AsyncStream { continuation in
            handle.readabilityHandler = { handle in
                let data = handle.availableData
                if data.isEmpty {  // EOF: the process closed its end
                    handle.readabilityHandler = nil
                    reader.finish().forEach { continuation.yield($0) }
                    continuation.finish()
                } else {
                    reader.append(data).forEach { continuation.yield($0) }
                }
            }
        }
    }

    func append(_ data: Data) -> [String] {
        lock.withLock {
            buffer.append(data)
            var lines: [String] = []
            while let newline = buffer.firstIndex(of: 0x0A) {
                lines.append(Self.decode(buffer[buffer.startIndex..<newline]))
                buffer.removeSubrange(buffer.startIndex...newline)
            }
            return lines
        }
    }

    func finish() -> [String] {
        lock.withLock {
            defer { buffer.removeAll() }
            return buffer.isEmpty ? [] : [Self.decode(buffer[...])]
        }
    }

    private static func decode(_ bytes: Data.SubSequence) -> String {
        var line = String(decoding: bytes, as: UTF8.self)
        if line.hasSuffix("\r") { line.removeLast() }
        return line
    }
}
