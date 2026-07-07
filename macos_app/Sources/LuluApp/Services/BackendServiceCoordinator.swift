import Foundation

enum BackendServiceError: LocalizedError {
    case pythonMissing(String)
    case startupTimedOut(String)
    case processExited(String)
    case startupHandshakeFailed(String)
    case badHTTPStatus(Int, String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .pythonMissing(let path):
            return "Lulu could not find the configured Python runtime at \(path)."
        case .startupTimedOut(let details):
            return details.isEmpty
                ? "The local backend service did not become healthy in time."
                : "The local backend service did not become healthy in time. \(details)"
        case .processExited(let details):
            return details.isEmpty
                ? "The local backend service exited before it became healthy."
                : "The local backend service exited before it became healthy. \(details)"
        case .startupHandshakeFailed(let details):
            return details.isEmpty
                ? "The local backend service did not provide a valid startup handshake."
                : "The local backend service did not provide a valid startup handshake. \(details)"
        case .badHTTPStatus(let statusCode, let details):
            let summary = "The backend service returned HTTP \(statusCode)."
            guard !details.isEmpty else {
                return summary
            }
            return "\(summary) \(details)"
        case .invalidResponse:
            return "The backend service returned an invalid HTTP response."
        }
    }
}

actor BackendServiceCoordinator {
    private static let startupPrefix = "LULU_BACKEND_STARTUP:"

    private struct StartupHandshake {
        let payload: BackendStartupPayload
        let trailingOutput: String
    }

    private let configuration: BackendConfiguration
    private let session: URLSession
    private var process: Process?
    private var outputPipe: Pipe?
    private var boundPort: Int?
    private var bufferedLogs = ""

    init(configuration: BackendConfiguration = .default()) {
        self.configuration = configuration
        let sessionConfiguration = URLSessionConfiguration.default
        sessionConfiguration.timeoutIntervalForRequest = 10
        sessionConfiguration.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: sessionConfiguration)
    }

    func launchIfNeeded() throws {
        if process?.isRunning == true, boundPort != nil {
            return
        }
        guard FileManager.default.fileExists(atPath: configuration.pythonExecutable.path) else {
            throw BackendServiceError.pythonMissing(configuration.pythonExecutable.path)
        }
        let process = Process()
        process.executableURL = configuration.pythonExecutable
        process.arguments = [
            "-m",
            "backend_service.service_runner",
            "--host",
            configuration.host,
            "--port",
            "0",
        ]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = configuration.backendRoot.path
        environment["LULU_SERVICE_LAUNCH_TOKEN"] = configuration.launchToken
        environment["LULU_SERVICE_STARTUP_NONCE"] = configuration.startupNonce
        environment["LULU_SERVICE_STARTUP_CONTRACT"] = configuration.startupContractVersion
        environment["LULU_PATH_MODE"] = configuration.pathMode
        if let appSupportDirectory = configuration.appSupportDirectory {
            environment["LULU_APP_SUPPORT_DIR"] = appSupportDirectory.path
        }
        environment["VIRTUAL_ENV"] = configuration.virtualEnvPath.path
        let existingPath = environment["PATH"] ?? ""
        environment["PATH"] = "\(configuration.virtualEnvPath.appendingPathComponent("bin").path):\(existingPath)"
        process.environment = environment
        process.currentDirectoryURL = configuration.backendRoot
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        self.process = process
        self.outputPipe = pipe
        let startupHandshake = try readStartupPayload(from: pipe.fileHandleForReading, process: process)
        let startupPayload = startupHandshake.payload
        guard startupPayload.contractVersion == configuration.startupContractVersion else {
            throw BackendServiceError.startupHandshakeFailed(
                "Expected contract \(configuration.startupContractVersion), got \(startupPayload.contractVersion)."
            )
        }
        guard startupPayload.startupNonce == configuration.startupNonce else {
            throw BackendServiceError.startupHandshakeFailed("Startup nonce mismatch.")
        }
        guard startupPayload.host == configuration.host else {
            throw BackendServiceError.startupHandshakeFailed("Unexpected startup host \(startupPayload.host).")
        }
        guard startupPayload.service == "lulu-backend" else {
            throw BackendServiceError.startupHandshakeFailed("Unexpected startup service \(startupPayload.service).")
        }
        self.boundPort = startupPayload.port
        if !startupHandshake.trailingOutput.isEmpty {
            bufferedLogs += startupHandshake.trailingOutput
        }
        startStreamingLogs(from: pipe.fileHandleForReading)
    }

    func shutdown() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        process?.terminate()
        process = nil
        outputPipe = nil
        boundPort = nil
        bufferedLogs = ""
    }

    func capturedLogs() -> String {
        return bufferedLogs
    }

    private func appendLogs(from data: Data) {
        guard !data.isEmpty else {
            return
        }
        bufferedLogs += String(decoding: data, as: UTF8.self)
    }

    private func startupContextSummary() -> String {
        var details: [String] = []
        details.append("Launch mode: \(configuration.launchMode.rawValue)")
        details.append("Python: \(configuration.pythonExecutable.path)")
        details.append("Backend root: \(configuration.backendRoot.path)")
        if let boundPort {
            details.append("Port: \(boundPort)")
        }
        return details.joined(separator: " ")
    }

    private func readProcessExitDetails() -> String {
        var details: [String] = [startupContextSummary()]
        guard let outputPipe else {
            return details.joined(separator: " ")
        }
        outputPipe.fileHandleForReading.readabilityHandler = nil
        let data = outputPipe.fileHandleForReading.readDataToEndOfFile()
        appendLogs(from: data)
        let logs = bufferedLogs.trimmingCharacters(in: .whitespacesAndNewlines)
        if !logs.isEmpty {
            details.append("Logs: \(logs)")
        }
        return details.joined(separator: " ")
    }

    private func readStartupPayload(from handle: FileHandle, process: Process) throws -> StartupHandshake {
        var pending = ""
        while true {
            if !process.isRunning {
                let trailing = handle.readDataToEndOfFile()
                appendLogs(from: trailing)
                throw BackendServiceError.processExited(readProcessExitDetails())
            }

            let data = handle.availableData
            if data.isEmpty {
                throw BackendServiceError.processExited(readProcessExitDetails())
            }

            pending += String(decoding: data, as: UTF8.self)
            while let newlineRange = pending.range(of: "\n") {
                let line = String(pending[..<newlineRange.lowerBound])
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                pending.removeSubrange(..<newlineRange.upperBound)
                if line.isEmpty {
                    continue
                }
                if line.hasPrefix(Self.startupPrefix) {
                    let payload = String(line.dropFirst(Self.startupPrefix.count))
                    guard let payloadData = payload.data(using: .utf8) else {
                        throw BackendServiceError.startupHandshakeFailed("Startup payload was not valid UTF-8.")
                    }
                    do {
                        return StartupHandshake(
                            payload: try JSONDecoder().decode(BackendStartupPayload.self, from: payloadData),
                            trailingOutput: pending
                        )
                    } catch {
                        throw BackendServiceError.startupHandshakeFailed("Could not decode startup payload.")
                    }
                }
                bufferedLogs += line + "\n"
            }
        }
    }

    private func startStreamingLogs(from handle: FileHandle) {
        handle.readabilityHandler = { [weak self] readableHandle in
            let data = readableHandle.availableData
            Task {
                guard let self else {
                    readableHandle.readabilityHandler = nil
                    return
                }
                if data.isEmpty {
                    readableHandle.readabilityHandler = nil
                    return
                }
                await self.appendLogs(from: data)
            }
        }
    }

    func waitUntilHealthy(retries: Int = 60, delayNanoseconds: UInt64 = 500_000_000) async throws {
        for _ in 0..<retries {
            if let process, !process.isRunning {
                throw BackendServiceError.processExited(readProcessExitDetails())
            }
            if let health = try? await fetchHealth(), health.ready {
                return
            }
            try await Task.sleep(nanoseconds: delayNanoseconds)
        }
        throw BackendServiceError.startupTimedOut(startupContextSummary())
    }

    func fetchHealth() async throws -> HealthResponse {
        let request = try makeRequest(path: "/healthz", method: "GET")
        return try await execute(request: request, decodeAs: HealthResponse.self)
    }

    func fetchDependencies() async throws -> DependencyHealthResponse {
        let request = try makeRequest(path: "/v1/dependencies", method: "GET")
        return try await execute(request: request, decodeAs: DependencyHealthResponse.self)
    }

    func fetchSettings() async throws -> SettingsResponse {
        let request = try makeRequest(path: "/v1/settings", method: "GET")
        return try await execute(request: request, decodeAs: SettingsResponse.self)
    }

    func saveSettings(_ draft: SettingsDraft) async throws -> SettingsUpdateResponse {
        var request = try makeRequest(path: "/v1/settings", method: "PUT")
        request.httpBody = try JSONEncoder().encode(draft.updateRequest)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: SettingsUpdateResponse.self)
    }

    func fetchRuntimeState() async throws -> RuntimeStateResponse {
        let request = try makeRequest(path: "/v1/runtime/state", method: "GET")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func fetchRuntimeDiagnostics() async throws -> RuntimeDiagnosticsResponse {
        let request = try makeRequest(path: "/v1/runtime/diagnostics", method: "GET")
        return try await execute(request: request, decodeAs: RuntimeDiagnosticsResponse.self)
    }

    func startRuntime(mode: String) async throws -> RuntimeStateResponse {
        var request = try makeRequest(path: "/v1/runtime/start", method: "POST")
        request.httpBody = try JSONEncoder().encode(["mode": mode])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func stopRuntime() async throws -> RuntimeStateResponse {
        let request = try makeRequest(path: "/v1/runtime/stop", method: "POST")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func restartRuntime(mode: String) async throws -> RuntimeStateResponse {
        var request = try makeRequest(path: "/v1/runtime/restart", method: "POST")
        request.httpBody = try JSONEncoder().encode(["mode": mode])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func createPDFJob(_ requestBody: PDFJobCreateRequest) async throws -> PDFJobResponse {
        var request = try makeRequest(path: "/v1/pdf-audiobook/jobs", method: "POST")
        request.httpBody = try JSONEncoder().encode(requestBody)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: PDFJobResponse.self)
    }

    func fetchPDFJob(jobID: String) async throws -> PDFJobResponse {
        let request = try makeRequest(path: "/v1/pdf-audiobook/jobs/\(jobID)", method: "GET")
        return try await execute(request: request, decodeAs: PDFJobResponse.self)
    }

    func connectEvents(onEvent: @escaping @Sendable (RuntimeEventEnvelope) -> Void) throws -> Task<Void, Never> {
        let request = try makeRequest(url: webSocketURL(), method: "GET")
        let webSocket = session.webSocketTask(with: request)
        webSocket.resume()
        return Task {
            defer { webSocket.cancel(with: .goingAway, reason: nil) }
            while !Task.isCancelled {
                do {
                    let message = try await webSocket.receive()
                    switch message {
                    case .string(let text):
                        let data = Data(text.utf8)
                        let envelope = try JSONDecoder().decode(RuntimeEventEnvelope.self, from: data)
                        onEvent(envelope)
                    case .data(let data):
                        let envelope = try JSONDecoder().decode(RuntimeEventEnvelope.self, from: data)
                        onEvent(envelope)
                    @unknown default:
                        continue
                    }
                } catch {
                    break
                }
            }
        }
    }

    private func serviceBaseURL() throws -> URL {
        guard let boundPort else {
            throw BackendServiceError.startupHandshakeFailed("Backend port was not negotiated.")
        }
        guard let baseURL = URL(string: "http://\(configuration.host):\(boundPort)") else {
            throw BackendServiceError.startupHandshakeFailed("Backend base URL could not be constructed.")
        }
        return baseURL
    }

    private func webSocketURL() throws -> URL {
        guard let boundPort else {
            throw BackendServiceError.startupHandshakeFailed("Backend port was not negotiated.")
        }
        guard let socketURL = URL(string: "ws://\(configuration.host):\(boundPort)/v1/events/ws") else {
            throw BackendServiceError.startupHandshakeFailed("Backend WebSocket URL could not be constructed.")
        }
        return socketURL
    }

    private func makeRequest(path: String, method: String) throws -> URLRequest {
        try makeRequest(url: serviceBaseURL().appending(path: path), method: method)
    }

    private func makeRequest(url: URL, method: String) throws -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(configuration.launchToken)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func execute<T: Decodable>(request: URLRequest, decodeAs type: T.Type) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw BackendServiceError.invalidResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let details = String(decoding: data, as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            throw BackendServiceError.badHTTPStatus(httpResponse.statusCode, details)
        }
        return try JSONDecoder().decode(type, from: data)
    }
}
