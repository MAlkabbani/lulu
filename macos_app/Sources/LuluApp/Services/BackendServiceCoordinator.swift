import Foundation

enum BackendServiceError: LocalizedError {
    case pythonMissing(String)
    case startupTimedOut(String)
    case processExited(String)
    case badHTTPStatus(Int, String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .pythonMissing(let path):
            return "Lulu could not find the repo-local Python runtime at \(path)."
        case .startupTimedOut(let details):
            return details.isEmpty
                ? "The local backend service did not become healthy in time."
                : "The local backend service did not become healthy in time. \(details)"
        case .processExited(let details):
            return details.isEmpty
                ? "The local backend service exited before it became healthy."
                : "The local backend service exited before it became healthy. \(details)"
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
    private let configuration: BackendConfiguration
    private let session: URLSession
    private var process: Process?
    private var outputPipe: Pipe?

    init(configuration: BackendConfiguration = .default()) {
        self.configuration = configuration
        let sessionConfiguration = URLSessionConfiguration.default
        sessionConfiguration.timeoutIntervalForRequest = 10
        sessionConfiguration.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: sessionConfiguration)
    }

    func launchIfNeeded() throws {
        if process?.isRunning == true {
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
            String(configuration.port),
        ]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = configuration.repoRoot.path
        environment["LULU_SERVICE_LAUNCH_TOKEN"] = configuration.launchToken
        environment["VIRTUAL_ENV"] = configuration.virtualEnvPath.path
        let existingPath = environment["PATH"] ?? ""
        environment["PATH"] = "\(configuration.virtualEnvPath.appendingPathComponent("bin").path):\(existingPath)"
        process.environment = environment
        process.currentDirectoryURL = configuration.repoRoot
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        self.process = process
        self.outputPipe = pipe
    }

    func shutdown() {
        process?.terminate()
        process = nil
        outputPipe = nil
    }

    func capturedLogs() -> String {
        guard let outputPipe else {
            return ""
        }
        let data = outputPipe.fileHandleForReading.availableData
        guard !data.isEmpty else {
            return ""
        }
        return String(decoding: data, as: UTF8.self)
    }

    private func startupContextSummary() -> String {
        var details: [String] = []
        details.append("Python: \(configuration.pythonExecutable.path)")
        details.append("Repo: \(configuration.repoRoot.path)")
        return details.joined(separator: " ")
    }

    private func readProcessExitDetails() -> String {
        var details: [String] = [startupContextSummary()]
        guard let outputPipe else {
            return details.joined(separator: " ")
        }
        let data = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let logs = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        if !logs.isEmpty {
            details.append("Logs: \(logs)")
        }
        return details.joined(separator: " ")
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
        let request = makeRequest(path: "/healthz", method: "GET")
        return try await execute(request: request, decodeAs: HealthResponse.self)
    }

    func fetchDependencies() async throws -> DependencyHealthResponse {
        let request = makeRequest(path: "/v1/dependencies", method: "GET")
        return try await execute(request: request, decodeAs: DependencyHealthResponse.self)
    }

    func fetchSettings() async throws -> SettingsResponse {
        let request = makeRequest(path: "/v1/settings", method: "GET")
        return try await execute(request: request, decodeAs: SettingsResponse.self)
    }

    func saveSettings(_ draft: SettingsDraft) async throws -> SettingsUpdateResponse {
        var request = makeRequest(path: "/v1/settings", method: "PUT")
        request.httpBody = try JSONEncoder().encode(draft.updateRequest)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: SettingsUpdateResponse.self)
    }

    func fetchRuntimeState() async throws -> RuntimeStateResponse {
        let request = makeRequest(path: "/v1/runtime/state", method: "GET")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func fetchRuntimeDiagnostics() async throws -> RuntimeDiagnosticsResponse {
        let request = makeRequest(path: "/v1/runtime/diagnostics", method: "GET")
        return try await execute(request: request, decodeAs: RuntimeDiagnosticsResponse.self)
    }

    func startRuntime(mode: String) async throws -> RuntimeStateResponse {
        var request = makeRequest(path: "/v1/runtime/start", method: "POST")
        request.httpBody = try JSONEncoder().encode(["mode": mode])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func stopRuntime() async throws -> RuntimeStateResponse {
        let request = makeRequest(path: "/v1/runtime/stop", method: "POST")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func restartRuntime(mode: String) async throws -> RuntimeStateResponse {
        var request = makeRequest(path: "/v1/runtime/restart", method: "POST")
        request.httpBody = try JSONEncoder().encode(["mode": mode])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await execute(request: request, decodeAs: RuntimeStateResponse.self)
    }

    func connectEvents(onEvent: @escaping @Sendable (RuntimeEventEnvelope) -> Void) -> Task<Void, Never> {
        let request = makeRequest(url: configuration.webSocketURL, method: "GET")
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

    private func makeRequest(path: String, method: String) -> URLRequest {
        makeRequest(url: configuration.baseURL.appending(path: path), method: method)
    }

    private func makeRequest(url: URL, method: String) -> URLRequest {
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
