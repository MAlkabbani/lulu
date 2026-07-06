import Foundation

struct BackendConfiguration {
    let repoRoot: URL
    let virtualEnvPath: URL
    let pythonExecutable: URL
    let host: String
    let launchToken: String
    let startupNonce: String
    let startupContractVersion: String

    static func `default`() -> BackendConfiguration {
        let packageDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // Services
            .deletingLastPathComponent() // LuluApp
            .deletingLastPathComponent() // Sources
            .deletingLastPathComponent() // macos_app
        let repoRoot = packageDirectory.deletingLastPathComponent()
        let virtualEnvPath = repoRoot.appendingPathComponent(".venv")
        let environment = ProcessInfo.processInfo.environment
        let token = ProcessInfo.processInfo.environment["LULU_DESKTOP_LAUNCH_TOKEN"] ?? UUID().uuidString
        return BackendConfiguration(
            repoRoot: repoRoot,
            virtualEnvPath: virtualEnvPath,
            pythonExecutable: resolvePythonExecutable(repoRoot: repoRoot, virtualEnvPath: virtualEnvPath),
            host: "127.0.0.1",
            launchToken: token,
            startupNonce: UUID().uuidString,
            startupContractVersion: environment["LULU_DESKTOP_STARTUP_CONTRACT"] ?? "v1"
        )
    }

    private static func resolvePythonExecutable(repoRoot: URL, virtualEnvPath: URL) -> URL {
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

        return virtualEnvPath.appendingPathComponent("bin/python")
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
