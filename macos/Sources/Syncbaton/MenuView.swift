import SwiftUI
import SyncbatonCore

struct MenuView: View {
    @Bindable var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !model.isConfigured {
                Text("Set the server URL and your token to get started.").foregroundStyle(.secondary)
            } else if let error = model.connectionError {
                Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.red).font(.callout)
            }
            if let error = model.localError {
                Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.orange).font(.callout)
            }
            ForEach(model.info?.folders ?? []) { folder in
                VStack(alignment: .leading, spacing: 6) {
                    Text(folder.id).font(.headline)
                    let list = model.projects[folder.id] ?? []
                    if list.isEmpty {
                        Text("No projects").foregroundStyle(.secondary).font(.callout)
                    }
                    ForEach(list) { project in
                        ProjectRow(model: model, folder: folder.id, project: project)
                    }
                }
                Divider()
            }
            HStack {
                Button("Refresh") { Task { await model.refresh() } }
                Button("Settings…") {
                    openWindow(id: "settings")
                    NSApp.activate()
                }
                Spacer()
                Button("Quit") { model.requestQuit() }
            }
        }
        .padding(14)
        .frame(width: 380)
    }
}

struct ProjectRow: View {
    @Bindable var model: AppModel
    let folder: String
    let project: Project

    private var key: String { AppModel.key(folder, project.name) }
    private var phase: SessionPhase { model.phase(folder, project.name) }
    private var me: String? { model.me }
    private var heldByMe: Bool { project.holder != nil && project.holder == me }
    private var heldByOther: Bool { project.holder != nil && project.holder != me }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(project.displayName).fontWeight(.medium)
                if model.warnings[key] != nil {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        .help("Open here while checked out by someone else. Close it without saving.")
                }
                Spacer()
                Text(stateText).font(.callout).foregroundStyle(project.stale && heldByOther ? .orange : .secondary)
            }
            if let last = project.lastCheckin {
                Text(
                    "Last: \(last.user), \(last.at.formatted(date: .abbreviated, time: .shortened))\(last.note.isEmpty ? "" : " — \(last.note)")"
                )
                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            controls
        }
    }

    private var stateText: String {
        guard let holder = project.holder else { return "Free" }
        let since = project.since.map { " since " + $0.formatted(date: .omitted, time: .shortened) } ?? ""
        if holder == me { return "Held by you" + since }
        return "Held by \(holder)\(since)" + (project.stale ? " (stale)" : "")
    }

    @ViewBuilder private var controls: some View {
        switch phase {
        case .checkingOut:
            ProgressView().controlSize(.small)
        case .downloading(let p):
            HStack {
                ProgressView("Downloading", value: p).font(.caption)
                Button("Cancel") { model.cancel(folder: folder, project: project.name) }.controlSize(.small)
            }
        case .open:
            HStack {
                Text("Open").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Done") { model.done(folder: folder, project: project.name) }
            }
        case .closing:
            HStack {
                TextField(
                    "What changed?", text: Binding(get: { model.notes[key] ?? "" }, set: { model.notes[key] = $0 })
                )
                .textFieldStyle(.roundedBorder)
                .onSubmit { model.submit(folder: folder, project: project.name) }
                Button("Check in") { model.submit(folder: folder, project: project.name) }
            }
        case .uploading(let p):
            HStack {
                ProgressView("Uploading", value: p).font(.caption)
                Button("Cancel") { model.cancel(folder: folder, project: project.name) }.controlSize(.small)
            }
        case .checkingIn:
            ProgressView("Waiting for the server").controlSize(.small).font(.caption)
        case .free, .failed:
            idleControls
        }
    }

    @ViewBuilder private var idleControls: some View {
        if case .failed(let failure) = phase {
            HStack(alignment: .top) {
                Text(failure.message).font(.caption).foregroundStyle(.red)
                Spacer()
                Button("Dismiss") { model.dismiss(folder: folder, project: project.name) }.controlSize(.small)
            }
        }
        HStack {
            Spacer()
            if heldByMe {
                Button("Check in…") { model.done(folder: folder, project: project.name) }
            }
            if heldByOther && project.stale {
                Button("Take over") { model.open(folder: folder, project: project.name, takeOver: true) }
            } else if !heldByOther {
                Button("Open") { model.open(folder: folder, project: project.name, takeOver: false) }
            }
        }
    }
}
