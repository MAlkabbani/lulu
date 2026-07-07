import Foundation

enum UserFacingText {
    static let noDataYet = "No data yet."
    static let noActivityYet = "No activity yet."
    static let notLoadedYet = "Not loaded yet."
    static let backendUnavailable = "Backend unavailable."
    static let backendReady = "Backend ready."

    static func availabilityLabel(_ available: Bool?) -> String {
        available == true ? "Available" : "Unavailable"
    }

    static func runtimeModeLabel(_ mode: String?) -> String {
        switch mode {
        case "continuous":
            return "Continuous Voice"
        case "turn-based":
            return "Turn-Based Voice"
        default:
            return "Loading"
        }
    }

    static func runtimePhaseLabel(_ phase: String?) -> String {
        switch phase {
        case "starting":
            return "Starting"
        case "ready":
            return "Ready"
        case "passive_listening":
            return "Listening for Wake Phrase"
        case "listening":
            return "Listening"
        case "wake_detected":
            return "Matching Wake Phrase"
        case "conversation_window":
            return "Conversation Window"
        case "cooldown":
            return "Cooldown"
        case "transcribing":
            return "Transcribing"
        case "thinking":
            return "Thinking"
        case "streaming":
            return "Generating Response"
        case "speaking":
            return "Speaking"
        case "idle":
            return "Idle"
        case "startup_error", "capture_error", "router_error", "stt_error", "tts_error", "stream_error", "runtime_error":
            return "Error"
        default:
            return "Loading"
        }
    }

    static func microphoneStatusLabel(_ status: String) -> String {
        switch status {
        case "authorized":
            return "Allowed"
        case "denied":
            return "Denied"
        case "restricted":
            return "Restricted"
        case "not_determined":
            return "Not Requested Yet"
        default:
            return "Unknown"
        }
    }

    static func textOrFallback(_ text: String?, fallback: String = noDataYet) -> String {
        let trimmed = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallback : trimmed
    }

    static func pdfJobStatusLabel(_ status: String?) -> String {
        switch status {
        case "pending":
            return "Pending"
        case "running":
            return "Running"
        case "completed":
            return "Completed"
        case "failed":
            return "Failed"
        default:
            return "No data yet."
        }
    }

    static func pdfWorkflowModeLabel(dryRun: Bool) -> String {
        dryRun ? "Dry Run" : "Export"
    }

    static func booleanStatusLabel(_ value: Bool) -> String {
        value ? "Ready" : "Needs Attention"
    }

    static func launchModeLabel(_ pathMode: String?) -> String {
        pathMode == "app_support" ? "Packaged App Mode" : "Preview Checkout Mode"
    }
}
