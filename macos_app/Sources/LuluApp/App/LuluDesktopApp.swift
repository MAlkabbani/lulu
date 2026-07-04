import AppKit
import SwiftUI

@main
struct LuluDesktopApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("Lulu Desktop") {
            ContentView(model: model)
                .task {
                    await model.bootstrap()
                }
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                    Task {
                        await model.shutdown()
                    }
                }
        }
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("Refresh Diagnostics") {
                    Task {
                        await model.refreshDiagnostics()
                    }
                }
            }
        }

        Settings {
            SettingsView(model: model)
                .frame(width: 700)
        }
    }
}

