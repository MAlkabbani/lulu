import Foundation

enum BackendLaunchMode: String {
    case preview = "preview"
    case packaged = "packaged"
}

struct BackendConfiguration {
    let launchMode: BackendLaunchMode
    let backendRoot: URL
    let virtualEnvPath: URL
    let pythonExecutable: URL
    let pathMode: String
    let appSupportDirectory: URL?
    let cacheDirectory: URL?
    let host: String
    let launchToken: String
    let startupNonce: String
    let startupContractVersion: String

    static func `default`() -> BackendConfiguration {
        let environment = ProcessInfo.processInfo.environment
        let launchMode = resolveLaunchMode(environment: environment)
        let backendRoot = resolveBackendRoot(launchMode: launchMode, environment: environment)
        let virtualEnvPath = resolveVirtualEnvPath(
            launchMode: launchMode,
            backendRoot: backendRoot,
            environment: environment
        )
        let appSupportDirectory = resolveAppSupportDirectory(
            launchMode: launchMode,
            environment: environment
        )
        let cacheDirectory = resolveCacheDirectory(
            launchMode: launchMode,
            environment: environment
        )
        let token = ProcessInfo.processInfo.environment["LULU_DESKTOP_LAUNCH_TOKEN"] ?? UUID().uuidString
        return BackendConfiguration(
            launchMode: launchMode,
            backendRoot: backendRoot,
            virtualEnvPath: virtualEnvPath,
            pythonExecutable: resolvePythonExecutable(backendRoot: backendRoot, virtualEnvPath: virtualEnvPath),
            pathMode: launchMode == .packaged ? "app_support" : "repo",
            appSupportDirectory: appSupportDirectory,
            cacheDirectory: cacheDirectory,
            host: "127.0.0.1",
            launchToken: token,
            startupNonce: UUID().uuidString,
            startupContractVersion: environment["LULU_DESKTOP_STARTUP_CONTRACT"] ?? "v1"
        )
    }

    private static func resolveLaunchMode(environment: [String: String]) -> BackendLaunchMode {
        if let raw = environment["LULU_DESKTOP_LAUNCH_MODE"]?.lowercased() {
            return raw == BackendLaunchMode.packaged.rawValue ? .packaged : .preview
        }
        let packagedBackendRoot = Bundle.main.resourceURL?.appendingPathComponent("backend")
        if let packagedBackendRoot, FileManager.default.fileExists(atPath: packagedBackendRoot.path) {
            return .packaged
        }
        return .preview
    }

    private static func resolveBackendRoot(
        launchMode: BackendLaunchMode,
        environment: [String: String]
    ) -> URL {
        if let override = environment["LULU_DESKTOP_BACKEND_ROOT"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        switch launchMode {
        case .preview:
            let packageDirectory = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent() // Services
                .deletingLastPathComponent() // LuluApp
                .deletingLastPathComponent() // Sources
                .deletingLastPathComponent() // macos_app
            return packageDirectory.deletingLastPathComponent()
        case .packaged:
            if let resourceURL = Bundle.main.resourceURL {
                return resourceURL.appendingPathComponent("backend")
            }
            return URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
        }
    }

    private static func resolveVirtualEnvPath(
        launchMode: BackendLaunchMode,
        backendRoot: URL,
        environment: [String: String]
    ) -> URL {
        if let override = environment["LULU_DESKTOP_VENV"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        switch launchMode {
        case .preview:
            return backendRoot.appendingPathComponent(".venv")
        case .packaged:
            let runtimeRoot = backendRoot.appendingPathComponent("runtime", isDirectory: true)
            let runtimeBin = runtimeRoot.appendingPathComponent("bin", isDirectory: true)
            if FileManager.default.fileExists(atPath: runtimeBin.path) {
                return runtimeRoot
            }
            return backendRoot.appendingPathComponent(".venv")
        }
    }

    private static func resolveAppSupportDirectory(
        launchMode: BackendLaunchMode,
        environment: [String: String]
    ) -> URL? {
        if let override = environment["LULU_APP_SUPPORT_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        guard launchMode == .packaged else {
            return nil
        }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first?
            .appendingPathComponent("Lulu", isDirectory: true)
    }

    private static func resolveCacheDirectory(
        launchMode: BackendLaunchMode,
        environment: [String: String]
    ) -> URL? {
        if let override = environment["LULU_CACHE_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        guard launchMode == .packaged else {
            return nil
        }
        return FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)
            .first?
            .appendingPathComponent("Lulu", isDirectory: true)
    }

    private static func resolvePythonExecutable(backendRoot: URL, virtualEnvPath: URL) -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["LULU_DESKTOP_PYTHON"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }

        let fileManager = FileManager.default
        var candidates: [URL] = []

        let configPath = virtualEnvPath.appendingPathComponent("pyvenv.cfg")
        if
            let configText = try? String(contentsOf: configPath, encoding: .utf8),
            let version = parseConfigValue("version", from: configText)
        {
            let parts = version.split(separator: ".")
            if parts.count >= 2 {
                let majorMinor = "\(parts[0]).\(parts[1])"
                candidates.append(virtualEnvPath.appendingPathComponent("bin/python\(majorMinor)"))
            }
        }

        let binPath = virtualEnvPath.appendingPathComponent("bin")
        if let entries = try? fileManager.contentsOfDirectory(at: binPath, includingPropertiesForKeys: nil) {
            let discovered = entries
                .filter { $0.lastPathComponent.range(of: #"^python\d+\.\d+$"#, options: .regularExpression) != nil }
                .sorted { $0.lastPathComponent > $1.lastPathComponent }
            candidates.append(contentsOf: discovered)
        }

        candidates.append(virtualEnvPath.appendingPathComponent("bin/python"))
        candidates.append(virtualEnvPath.appendingPathComponent("bin/python3"))

        for candidate in deduplicated(candidates) where fileManager.isExecutableFile(atPath: candidate.path) {
            return candidate
        }

        let fallback = virtualEnvPath.appendingPathComponent("bin/python")
        if FileManager.default.isExecutableFile(atPath: fallback.path) {
            return fallback
        }
        return backendRoot.appendingPathComponent(".venv/bin/python")
    }

    private static func parseConfigValue(_ key: String, from configText: String) -> String? {
        for line in configText.split(whereSeparator: \.isNewline) {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            guard parts.count == 2, parts[0] == key else {
                continue
            }
            return parts[1]
        }
        return nil
    }

    private static func deduplicated(_ urls: [URL]) -> [URL] {
        var seen = Set<String>()
        var result: [URL] = []
        for url in urls where seen.insert(url.path).inserted {
            result.append(url)
        }
        return result
    }
}
