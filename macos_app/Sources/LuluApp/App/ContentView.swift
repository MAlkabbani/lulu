import SwiftUI

struct ContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        TabView {
            AssistantView(model: model)
                .tabItem {
                    Label("Assistant", systemImage: "message")
                }

            DiagnosticsView(model: model)
                .tabItem {
                    Label("Diagnostics", systemImage: "waveform.path.ecg")
                }

            PDFAudiobooksView(model: model)
                .tabItem {
                    Label("PDF Audiobooks", systemImage: "book.closed")
                }
        }
        .frame(minWidth: 960, minHeight: 700)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
