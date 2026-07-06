import Foundation

struct BackendStartupPayload: Decodable {
    let contractVersion: String
    let host: String
    let port: Int
    let startupNonce: String
    let service: String

    private enum CodingKeys: String, CodingKey {
        case contractVersion = "contract_version"
        case host
        case port
        case startupNonce = "startup_nonce"
        case service
    }
}

struct HealthResponse: Decodable {
    let apiVersion: String
    let status: String
    let service: String
    let ready: Bool

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case status
        case service
        case ready
    }
}

struct DependencyHealthResponse: Decodable {
    let apiVersion: String
    let ollamaReachable: Bool
    let ollamaVersion: String
    let chatModelAvailable: Bool
    let embeddingModelAvailable: Bool
    let audioInputAvailable: Bool
    let ttsAvailable: Bool
    let memoryPathAvailable: Bool
    let issues: [String]

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case ollamaReachable = "ollama_reachable"
        case ollamaVersion = "ollama_version"
        case chatModelAvailable = "chat_model_available"
        case embeddingModelAvailable = "embedding_model_available"
        case audioInputAvailable = "audio_input_available"
        case ttsAvailable = "tts_available"
        case memoryPathAvailable = "memory_path_available"
        case issues
    }
}

struct SettingsResponse: Decodable {
    let apiVersion: String
    let pathMode: String
    let configPath: String
    let chatModel: String
    let embeddingModel: String
    let whisperModel: String
    let whisperLanguage: String
    let chromaPath: String
    let logsPath: String
    let exportsPath: String
    let wakePhrase: String
    let practicalVoiceMode: Bool

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case pathMode = "path_mode"
        case configPath = "config_path"
        case chatModel = "chat_model"
        case embeddingModel = "embedding_model"
        case whisperModel = "whisper_model"
        case whisperLanguage = "whisper_language"
        case chromaPath = "chroma_path"
        case logsPath = "logs_path"
        case exportsPath = "exports_path"
        case wakePhrase = "wake_phrase"
        case practicalVoiceMode = "practical_voice_mode"
    }
}

struct SettingsUpdateRequest: Encodable {
    let chatModel: String
    let embeddingModel: String
    let whisperModel: String
    let whisperLanguage: String
    let wakePhrase: String
    let practicalVoiceMode: Bool

    private enum CodingKeys: String, CodingKey {
        case chatModel = "chat_model"
        case embeddingModel = "embedding_model"
        case whisperModel = "whisper_model"
        case whisperLanguage = "whisper_language"
        case wakePhrase = "wake_phrase"
        case practicalVoiceMode = "practical_voice_mode"
    }
}

struct SettingsUpdateResponse: Decodable {
    let apiVersion: String
    let saved: Bool
    let restartRequired: Bool
    let configPath: String

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case saved
        case restartRequired = "restart_required"
        case configPath = "config_path"
    }
}

struct RuntimeStateResponse: Decodable {
    let apiVersion: String
    let mode: String
    let runtimeMode: String
    let statusLine: String
    let degraded: Bool
    let lastError: String

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case mode
        case runtimeMode = "runtime_mode"
        case statusLine = "status_line"
        case degraded
        case lastError = "last_error"
    }
}

struct RuntimeDiagnosticsResponse: Decodable {
    let apiVersion: String
    let mode: String
    let runtimeMode: String
    let statusLine: String
    let lastError: String
    let runtimeActive: Bool
    let transcript: String
    let response: String
    let invocationSummary: String
    let actionSummary: String
    let currentToolStatus: String
    let memoryHitCount: Int
    let emittedChunkCount: Int
    let spokenChunkCount: Int
    let emittedCharCount: Int
    let spokenCharCount: Int
    let lastEmittedChunk: String
    let lastSpokenChunk: String
    let playbackGapCount: Int
    let tailMergeCount: Int
    let recentSaves: [String]
    let recentEvents: [String]
    let recentWakeAttempts: [String]
    let latenciesMS: [String: Double]
    let conversationWindowRemaining: Double?
    let cooldownRemaining: Double?
    let wakeGuidance: String
    let lastWakeScore: Double?
    let lastWakeDecision: String
    let wakeScoreThreshold: Double?
    let acceptedWakeAttempts: Int
    let rejectedWakeAttempts: Int
    let lastWakeConfidence: Double?
    let lastWakeAcousticScore: Double?
    let lastWakeDTWScore: Double?
    let lastWakeSNRDB: Double?
    let lastWakeFeatureFrames: Int

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case mode
        case runtimeMode = "runtime_mode"
        case statusLine = "status_line"
        case lastError = "last_error"
        case runtimeActive = "runtime_active"
        case transcript
        case response
        case invocationSummary = "invocation_summary"
        case actionSummary = "action_summary"
        case currentToolStatus = "current_tool_status"
        case memoryHitCount = "memory_hit_count"
        case emittedChunkCount = "emitted_chunk_count"
        case spokenChunkCount = "spoken_chunk_count"
        case emittedCharCount = "emitted_char_count"
        case spokenCharCount = "spoken_char_count"
        case lastEmittedChunk = "last_emitted_chunk"
        case lastSpokenChunk = "last_spoken_chunk"
        case playbackGapCount = "playback_gap_count"
        case tailMergeCount = "tail_merge_count"
        case recentSaves = "recent_saves"
        case recentEvents = "recent_events"
        case recentWakeAttempts = "recent_wake_attempts"
        case latenciesMS = "latencies_ms"
        case conversationWindowRemaining = "conversation_window_remaining"
        case cooldownRemaining = "cooldown_remaining"
        case wakeGuidance = "wake_guidance"
        case lastWakeScore = "last_wake_score"
        case lastWakeDecision = "last_wake_decision"
        case wakeScoreThreshold = "wake_score_threshold"
        case acceptedWakeAttempts = "accepted_wake_attempts"
        case rejectedWakeAttempts = "rejected_wake_attempts"
        case lastWakeConfidence = "last_wake_confidence"
        case lastWakeAcousticScore = "last_wake_acoustic_score"
        case lastWakeDTWScore = "last_wake_dtw_score"
        case lastWakeSNRDB = "last_wake_snr_db"
        case lastWakeFeatureFrames = "last_wake_feature_frames"
    }
}

enum JSONValue: Codable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON payload value."
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let value) = self {
            return value
        }
        return nil
    }

    var boolValue: Bool? {
        if case .bool(let value) = self {
            return value
        }
        return nil
    }

    var numberValue: Double? {
        if case .number(let value) = self {
            return value
        }
        return nil
    }

    var stringArrayValue: [String]? {
        guard case .array(let values) = self else {
            return nil
        }
        return values.compactMap(\.stringValue)
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self {
            return value
        }
        return nil
    }
}

struct RuntimeEventEnvelope: Decodable, Sendable {
    let apiVersion: String
    let eventType: String
    let timestamp: String
    let payload: [String: JSONValue]

    private enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case eventType = "event_type"
        case timestamp
        case payload
    }
}

struct SettingsDraft {
    var chatModel: String = ""
    var embeddingModel: String = ""
    var whisperModel: String = ""
    var whisperLanguage: String = ""
    var wakePhrase: String = ""
    var practicalVoiceMode: Bool = true

    init() {}

    init(from response: SettingsResponse) {
        chatModel = response.chatModel
        embeddingModel = response.embeddingModel
        whisperModel = response.whisperModel
        whisperLanguage = response.whisperLanguage
        wakePhrase = response.wakePhrase
        practicalVoiceMode = response.practicalVoiceMode
    }

    var updateRequest: SettingsUpdateRequest {
        SettingsUpdateRequest(
            chatModel: chatModel,
            embeddingModel: embeddingModel,
            whisperModel: whisperModel,
            whisperLanguage: whisperLanguage,
            wakePhrase: wakePhrase,
            practicalVoiceMode: practicalVoiceMode
        )
    }
}

struct WakeAttemptSnapshot {
    var transcript: String = ""
    var reason: String = ""
    var score: Double = 0
    var accepted: Bool = false
    var decision: String = "No wake attempts yet."
    var acceptedCount: Int = 0
    var rejectedCount: Int = 0
}

struct WakeSignalSnapshot {
    var confidence: Double?
    var threshold: Double?
    var acousticScore: Double?
    var dtwScore: Double?
    var snrDB: Double?
    var featureFrames: Int = 0
}

struct TTSProgressSnapshot {
    var emittedChunkCount: Int = 0
    var emittedCharCount: Int = 0
    var spokenChunkCount: Int = 0
    var spokenCharCount: Int = 0
    var lastEmittedChunk: String = ""
    var lastSpokenChunk: String = ""
}

struct VoicePreflightSnapshot {
    var microphoneStatus: String = "unknown"
    var backendAudioInputAvailable = false
    var ttsAvailable = false
    var guidance = "Microphone access has not been checked yet."
}
