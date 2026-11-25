import SwiftUI

@main
struct SyncbatonApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        MenuBarExtra {
            MenuView(model: model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)

        Window("Syncbaton Settings", id: "settings") {
            SettingsView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

/// The label is created at launch, so it starts the model and opens Settings on first run.
struct MenuBarLabel: View {
    let model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Image(systemName: model.statusSymbol)
            .task {
                if model.start() {
                    openWindow(id: "settings")
                    NSApp.activate()
                }
            }
    }
}
