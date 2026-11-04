import AVFoundation
import Foundation
import Observation

/// One AVPlayer with its current time (for highlighting the line being played) and
/// "play this range, then pause" (for a beat or a line).
@MainActor
@Observable
final class PlayerController {
    static let tickSec = 0.2

    let player = AVPlayer()
    private(set) var currentTime: Double = 0
    private(set) var loadedURL: URL?

    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var boundaryObserver: Any?

    init() {
        let interval = CMTime(seconds: Self.tickSec, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            MainActor.assumeIsolated { self?.currentTime = time.seconds.isFinite ? time.seconds : 0 }
        }
    }

    /// Opens `url` (no-op when it is already open); `nil` clears the player.
    func load(_ url: URL?, reload: Bool = false) {
        guard reload || url != loadedURL else { return }
        clearBoundary()
        loadedURL = url
        player.replaceCurrentItem(with: url.map { AVPlayerItem(url: $0) })
        currentTime = 0
    }

    func seek(to seconds: Double, play: Bool = true) {
        clearBoundary()
        player.seek(to: CMTime(seconds: max(0, seconds), preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        if play { player.play() }
    }

    /// Plays `start..<end` and pauses at `end`.
    func play(from start: Double, to end: Double) {
        seek(to: start)
        let stop = NSValue(time: CMTime(seconds: end, preferredTimescale: 600))
        boundaryObserver = player.addBoundaryTimeObserver(forTimes: [stop], queue: .main) { [weak self] in
            MainActor.assumeIsolated { self?.pause() }
        }
    }

    func pause() {
        player.pause()
        clearBoundary()
    }

    // The observers belong to `player`, which is released together with this controller.
    private func clearBoundary() {
        if let boundaryObserver { player.removeTimeObserver(boundaryObserver) }
        boundaryObserver = nil
    }
}
