import AVFoundation
import Foundation

enum MicrophoneAuthorizationService {
    static func currentStatus() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return "authorized"
        case .denied:
            return "denied"
        case .restricted:
            return "restricted"
        case .notDetermined:
            return "not_determined"
        @unknown default:
            return "unknown"
        }
    }

    static func requestAccessIfNeeded() async -> String {
        let existing = currentStatus()
        guard existing == "not_determined" else {
            return existing
        }
        let granted = await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
        return granted ? "authorized" : currentStatus()
    }
}

