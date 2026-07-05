import Darwin
import Foundation

struct BackendConfiguration {
    let repoRoot: URL
    let virtualEnvPath: URL
    let pythonExecutable: URL
    let host: String
    let port: Int
    let launchToken: String

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
        let preferredPort = environment["LULU_DESKTOP_BACKEND_PORT"]
            .flatMap(Int.init) ?? 8765
        let resolvedPort: Int
        if environment["LULU_DESKTOP_BACKEND_PORT"] != nil {
            resolvedPort = preferredPort
        } else {
            resolvedPort = resolveLaunchPort(host: "127.0.0.1", preferredPort: preferredPort)
        }
        return BackendConfiguration(
            repoRoot: repoRoot,
            virtualEnvPath: virtualEnvPath,
            pythonExecutable: resolvePythonExecutable(repoRoot: repoRoot, virtualEnvPath: virtualEnvPath),
            host: "127.0.0.1",
            port: resolvedPort,
            launchToken: token
        )
    }

    var baseURL: URL {
        URL(string: "http://\(host):\(port)")!
    }

    var webSocketURL: URL {
        URL(string: "ws://\(host):\(port)/v1/events/ws?token=\(launchToken)")!
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

    private static func resolveLaunchPort(host: String, preferredPort: Int) -> Int {
        if canBind(host: host, port: preferredPort) {
            return preferredPort
        }
        return allocateEphemeralPort(host: host) ?? preferredPort
    }

    private static func canBind(host: String, port: Int) -> Bool {
        guard let socketDescriptor = openBoundSocket(host: host, port: port) else {
            return false
        }
        close(socketDescriptor)
        return true
    }

    private static func allocateEphemeralPort(host: String) -> Int? {
        guard let socketDescriptor = openBoundSocket(host: host, port: 0) else {
            return nil
        }
        defer { close(socketDescriptor) }

        var address = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(socketDescriptor, $0, &length)
            }
        }
        guard result == 0 else {
            return nil
        }
        return Int(UInt16(bigEndian: address.sin_port))
    }

    private static func openBoundSocket(host: String, port: Int) -> Int32? {
        guard var address = socketAddress(host: host, port: port) else {
            return nil
        }

        let socketDescriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard socketDescriptor >= 0 else {
            return nil
        }

        var reuseAddress: Int32 = 1
        _ = withUnsafePointer(to: &reuseAddress) {
            setsockopt(
                socketDescriptor,
                SOL_SOCKET,
                SO_REUSEADDR,
                $0,
                socklen_t(MemoryLayout<Int32>.size)
            )
        }

        let bindResult = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(socketDescriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(socketDescriptor)
            return nil
        }
        return socketDescriptor
    }

    private static func socketAddress(host: String, port: Int) -> sockaddr_in? {
        guard port >= 0 && port <= Int(UInt16.max) else {
            return nil
        }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        let conversionResult = host.withCString { rawHost in
            inet_pton(AF_INET, rawHost, &address.sin_addr)
        }
        guard conversionResult == 1 else {
            return nil
        }
        return address
    }
}
