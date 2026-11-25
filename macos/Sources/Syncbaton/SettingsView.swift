import SwiftUI
import SyncbatonCore

struct SettingsView: View {
    let model: AppModel
    @State private var serverURL = ""
    @State private var token = ""
    @State private var interval = 15
    @State private var testResult: String?
    @State private var saveError: String?
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        Form {
            TextField("Server URL", text: $serverURL, prompt: Text("https://syncbaton.example"))
            SecureField("Your token", text: $token)
            Stepper("Refresh every \(interval) s", value: $interval, in: 5...120, step: 5)
            LabeledContent("Accessibility") {
                if model.accessibilityTrusted {
                    Text("Allowed")
                } else {
                    Button("Allow…") { LogicAdapter.requestAccessibility() }
                }
            }
            Text("Accessibility lets Syncbaton see when you close a Logic project. Without it you press Done yourself.")
                .font(.caption).foregroundStyle(.secondary)
            if let saveError { Text(saveError).font(.callout).foregroundStyle(.red) }
            HStack {
                Button("Test connection") {
                    testResult = "Testing…"
                    Task { testResult = await model.testConnection(serverURL: serverURL, token: token) }
                }
                if let testResult { Text(testResult).font(.callout).foregroundStyle(.secondary) }
                Spacer()
                Button("Save") {
                    saveError = model.apply(serverURL: serverURL, token: token, refreshInterval: interval)
                    if saveError == nil { dismissWindow(id: "settings") }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(serverURL.isEmpty || token.isEmpty)
            }
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .padding()
        .onAppear {
            serverURL = model.serverURLString
            token = model.token
            interval = model.refreshInterval
        }
    }
}
