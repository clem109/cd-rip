import AppKit
import SwiftUI

struct Edition: Identifiable {
    let id: Int
    let title: String
    let detail: String
}

struct AlbumTrack: Identifiable {
    let id: Int
    let title: String
    let duration: String
    var saved: Bool
    var imported: Bool
    var lyrics: Bool
}

final class Ripper: ObservableObject {
    @Published var running = false
    @Published var stopping = false
    @Published var checking = false
    @Published var phase = "Ready when you are"
    @Published var detail = "Start watching, then insert a CD."
    @Published var album = "Your CDs, beautifully archived."
    @Published var artist = "Apple Lossless · Artwork · Lyrics"
    @Published var fraction: Double = 0
    @Published var track = 0
    @Published var total = 0
    @Published var cover: NSImage?
    @Published var tracks: [AlbumTrack] = []
    @Published var releaseDetail = ""
    @Published var showSettings = false
    @Published var audioComplete = false
    @Published var editions: [Edition] = []
    @Published var logs: [String] = []
    @Published var showLog = false
    @Published var addToMusic = true
    @Published var fetchLyrics = true
    @Published var output: URL
    var process: Process?
    var input: Pipe?
    var folder: URL?
    var partial: URL?
    var expected: Double = 0
    var timer: Timer?
    var quitWhenDone = false
    private var lineBuffer = Data()
    private var config: [String: String] = [:]

    init() {
        if let url = Bundle.main.url(forResource: "config", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let values = try? JSONDecoder().decode([String: String].self, from: data) {
            config = values
        }
        let saved = UserDefaults.standard.string(forKey: "archive")
        output = URL(fileURLWithPath: saved ?? config["archive"] ??
                     NSHomeDirectory() + "/Music/CD Rip", isDirectory: true)
        addToMusic = UserDefaults.standard.object(forKey: "music") as? Bool ?? true
        fetchLyrics = UserDefaults.standard.object(forKey: "lyrics") as? Bool ?? true
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.updateProgress()
        }
    }

    func updateProgress() {
        refreshAlbum()
        if let partial = partial, expected > 0,
           let values = try? partial.resourceValues(forKeys: [.fileSizeKey]),
           let size = values.fileSize {
            let current = min(1, max(0, Double(size - 44) / expected))
            fraction = (Double(max(0, track - 1)) + current) / Double(max(1, total))
        }
        if cover == nil, let folder = folder {
            cover = NSImage(contentsOf: folder.appendingPathComponent("cover.jpg"))
        }
    }

    func restoreLatestAlbum() {
        let archive = output
        DispatchQueue.global(qos: .userInitiated).async {
            let urls = (try? FileManager.default.contentsOfDirectory(at: archive,
                            includingPropertiesForKeys: nil)) ?? []
            let latest = urls.filter { FileManager.default.fileExists(atPath: $0.appendingPathComponent("job.json").path) }
                .max { left, right in
                    let a = try? left.appendingPathComponent("job.json").resourceValues(forKeys: [.contentModificationDateKey])
                    let b = try? right.appendingPathComponent("job.json").resourceValues(forKeys: [.contentModificationDateKey])
                    return (a?.contentModificationDate ?? .distantPast) < (b?.contentModificationDate ?? .distantPast)
                }
            DispatchQueue.main.async {
                guard !self.running else { return }
                self.folder = latest
                self.refreshAlbum()
                if !self.tracks.isEmpty {
                    self.fraction = self.audioComplete ? 1 : Double(self.tracks.filter { $0.saved }.count) / Double(self.tracks.count)
                    self.phase = self.audioComplete ? "Album saved" : "Incomplete rip"
                    self.detail = self.tracks.allSatisfy { $0.imported } ? "All \(self.tracks.count) tracks added to Music." : "Saved in your archive."
                    if let folder = self.folder, let data = try? Data(contentsOf: folder.appendingPathComponent("job.json")),
                       let job = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let error = job["postprocess_error"] as? String { self.fail(error) }
                }
            }
        }
    }

    func refreshAlbum() {
        guard let folder = folder,
              let data = try? Data(contentsOf: folder.appendingPathComponent("job.json")),
              let job = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let meta = job["metadata"] as? [String: Any],
              let titles = meta["tracks"] as? [[String: Any]] else { return }
        let toc = job["toc"] as? [String: Any] ?? [:]
        let times = toc["tracks"] as? [[String: Any]] ?? []
        let files = job["files"] as? [[String: Any]] ?? []
        album = meta["album"] as? String ?? "Unknown album"
        artist = meta["artist"] as? String ?? "Unknown artist"
        audioComplete = job["audio_complete"] as? Bool ?? false
        total = titles.count
        releaseDetail = [meta["date"], meta["country"], meta["label"], meta["catalogue"]]
            .compactMap { $0 as? String }.filter { !$0.isEmpty }.joined(separator: " · ")
        tracks = titles.enumerated().map { index, item in
            let seconds = index < times.count ? Int((times[index]["sectors"] as? Double ?? 0) / 75) : 0
            return AlbumTrack(id: index + 1, title: item["title"] as? String ?? "Track \(index + 1)",
                duration: String(format: "%d:%02d", seconds / 60, seconds % 60),
                saved: index < files.count,
                imported: index < files.count && (files[index]["imported"] as? Bool ?? false),
                lyrics: FileManager.default.fileExists(atPath: folder.appendingPathComponent(String(format: "%02d.lyrics.txt", index + 1)).path))
        }
        if cover == nil { cover = NSImage(contentsOf: folder.appendingPathComponent("cover.jpg")) }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.directoryURL = output
        panel.prompt = "Use this folder"
        if panel.runModal() == .OK, let url = panel.url {
            output = url
            UserDefaults.standard.set(url.path, forKey: "archive")
        }
    }

    func openLibrary() {
        do {
            try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            NSWorkspace.shared.open(output)
        } catch { fail(error.localizedDescription) }
    }

    func log(_ text: String) {
        logs.append(text)
        if logs.count > 250 { logs.removeFirst(logs.count - 250) }
    }

    func fail(_ message: String) {
        phase = "Needs attention"
        detail = message
        log(message)
    }

    func launch(_ command: String) {
        guard !running && !checking else { return }
        let engine = Bundle.main.resourceURL!.appendingPathComponent("engine/cd-rip-engine")
        guard FileManager.default.isExecutableFile(atPath: engine.path) else {
            fail("The ripping engine is missing. Rebuild CD Rip.app.")
            return
        }
        let child = Process()
        child.executableURL = engine
        child.arguments = [command]
        if command != "doctor" {
            child.arguments! += ["--output", output.path]
            if !addToMusic { child.arguments!.append("--no-music") }
            if !fetchLyrics { child.arguments!.append("--no-lyrics") }
        }
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["CD_RIP_GUI"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        if let legacy = config["legacy_lock"] { env["CD_RIP_LEGACY_LOCK"] = legacy }
        child.environment = env
        let stdout = Pipe(), stdin = Pipe()
        child.standardOutput = stdout
        child.standardError = stdout
        child.standardInput = stdin
        input = stdin
        process = child
        lineBuffer = Data()
        stopping = false
        checking = command == "doctor"
        running = !checking
        phase = checking ? "Checking setup…" : "Starting…"
        detail = checking ? "No drive access is needed." : "Any CD already inserted is left alone in watch mode."
        UserDefaults.standard.set(addToMusic, forKey: "music")
        UserDefaults.standard.set(fetchLyrics, forKey: "lyrics")
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.receive(data) }
        }
        child.terminationHandler = { [weak self] task in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                guard let self = self else { return }
                self.running = false
                self.checking = false
                self.stopping = false
                self.editions = []
                self.partial = nil
                self.process = nil
                self.input = nil
                if task.terminationStatus == 0 {
                    if command == "doctor" {
                        self.phase = "Setup is ready"
                        self.detail = "Your Mac has everything needed to rip CDs."
                    } else if self.phase != "Needs attention" {
                        self.phase = "Ready when you are"
                        self.detail = "The watcher has stopped. Your saved tracks are safe."
                    }
                } else if self.phase != "Needs attention" {
                    self.fail("The ripper exited (\(task.terminationStatus)). Open the activity log for details.")
                }
                if self.quitWhenDone { NSApp.terminate(nil) }
            }
        }
        do { try child.run() }
        catch {
            running = false; checking = false; process = nil; input = nil
            fail(error.localizedDescription)
        }
    }

    func send(_ message: [String: Any]) {
        guard let pipe = input, let data = try? JSONSerialization.data(withJSONObject: message) else { return }
        do { try pipe.fileHandleForWriting.write(contentsOf: data + Data([10])) }
        catch { fail("Couldn't send the control to the ripper: \(error.localizedDescription)") }
    }

    func stop() {
        guard running else { return }
        stopping = true
        detail = "Finishing this CD, then stopping."
        send(["command": "stop"])
    }

    func choose(_ choice: Int?) {
        send(["command": "choose", "choice": choice.map(String.init) ?? ""])
        editions = []
        phase = "Identifying album…"
    }

    func importTrack(_ path: String) {
        let escaped = path.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\r", with: "\\r")
        let source = """
        set audioFile to POSIX file "\(escaped)" as alias
        with timeout of 300 seconds
            tell application "Music"
                activate
                set addedTrack to add audioFile
                return persistent ID of addedTrack
            end tell
        end timeout
        """
        DispatchQueue.global(qos: .userInitiated).async {
            var error: NSDictionary?
            let script = NSAppleScript(source: source)
            let result = script?.executeAndReturnError(&error)
            let ok = error == nil && !(result?.stringValue ?? "").isEmpty
            let message = error?[NSAppleScript.errorMessage] as? String
                ?? "Music did not confirm the import. Check Music before retrying."
            DispatchQueue.main.async {
                self.send(["command": "import_result", "path": path, "ok": ok,
                           "error": ok ? "" : message])
            }
        }
    }

    func receive(_ data: Data) {
        lineBuffer.append(data)
        while let newline = lineBuffer.firstIndex(of: 10) {
            let line = lineBuffer[..<newline]
            lineBuffer.removeSubrange(...newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
                if let text = String(data: line, encoding: .utf8), !text.isEmpty { log(text) }
                continue
            }
            switch event["event"] as? String {
            case "log":
                let message = event["message"] as? String ?? ""
                log(message)
                if message.hasPrefix("Error:") { fail(message) }
            case "waiting":
                if !stopping && phase != "Needs attention" {
                    phase = "Watching for CDs"
                    detail = "Insert the next CD whenever you're ready."
                }
            case "choices":
                let values = event["releases"] as? [[String: Any]] ?? []
                editions = values.enumerated().map { i, item in
                    Edition(id: i + 1, title: item["album"] as? String ?? "Album",
                            detail: [item["artist"], item["date"], item["country"], item["label"], item["catalogue"], item["disambiguation"]]
                                .compactMap { $0 as? String }.filter { !$0.isEmpty }.joined(separator: " · "))
                }
                phase = "Choose your edition"
                detail = "Several releases match this disc."
                NSApp.activate(ignoringOtherApps: true)
                (NSApp.delegate as? AppDelegate)?.showWindow()
            case "album":
                let meta = event["metadata"] as? [String: Any] ?? [:]
                album = meta["album"] as? String ?? "Unknown album"
                artist = meta["artist"] as? String ?? "Unknown artist"
                folder = (event["folder"] as? String).map { URL(fileURLWithPath: $0) }
                cover = nil; fraction = 0; track = 0; total = 0
                audioComplete = false
                refreshAlbum()
            case "track":
                track = event["number"] as? Int ?? 0
                total = event["total"] as? Int ?? 0
                phase = "Ripping track \(track) of \(total)"
                detail = event["title"] as? String ?? ""
                partial = (event["partial"] as? String).map { URL(fileURLWithPath: $0) }
                expected = event["expected"] as? Double ?? 0
            case "enriching":
                partial = nil; fraction = 1
                phase = "Adding the finishing touches"
                detail = "Finishing artwork and lyrics, then adding to Music."
            case "attention": fail(event["message"] as? String ?? "Check the activity log.")
            case "importing":
                phase = "Adding to Music"
                detail = event["name"] as? String ?? "Check for an Automation permission prompt."
            case "import_request":
                if let path = event["path"] as? String { importTrack(path) }
            case "complete":
                partial = nil; fraction = 1
                if let error = event["error"] as? String {
                    fail(error)
                    break
                }
                if phase == "Needs attention" { break }
                let warnings = event["warnings"] as? [String] ?? []
                let imported = event["imported"] as? Int ?? 0
                phase = "Album saved"
                detail = "\(imported) tracks added to Music" + (warnings.isEmpty ? "." : " · \(warnings.count) metadata notices in the log.")
                updateProgress()
            default: break
            }
        }
    }
}

struct ContentView: View {
    @ObservedObject var model: Ripper

    var body: some View {
        VStack(spacing: 0) {
            albumHeader
            Divider()
            if !model.editions.isEmpty {
                editionPicker
            } else if model.tracks.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "opticaldisc").font(.system(size: 48, weight: .ultraLight))
                        .foregroundStyle(.secondary)
                    Text("A home for your CD collection").font(.title3.weight(.semibold))
                    Text("Insert a CD to see its artwork, tracks and ripping progress.")
                        .foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                trackTable
            }
            Divider()
            progressFooter
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .frame(minWidth: 720, minHeight: 640)
        .sheet(isPresented: $model.showSettings) { settings }
        .sheet(isPresented: $model.showLog) { activity }
    }

    private var albumHeader: some View {
        HStack(alignment: .center, spacing: 28) {
            ZStack {
                RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor))
                if let image = model.cover {
                    Image(nsImage: image).resizable().scaledToFill()
                } else {
                    Image(systemName: "opticaldisc").font(.system(size: 70, weight: .ultraLight))
                        .foregroundStyle(.tertiary)
                }
            }
            .frame(width: 184, height: 184)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .shadow(color: .black.opacity(0.14), radius: 10, y: 4)
            .accessibilityLabel(model.cover == nil ? "Artwork not yet available" : "Album artwork")
            VStack(alignment: .leading, spacing: 10) {
                Text(model.tracks.isEmpty ? "Ready for your next CD" : model.album)
                    .font(.system(size: 27, weight: .bold)).fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                Text(model.tracks.isEmpty ? "An album at a time. Nothing lost." : model.artist)
                    .font(.title3).foregroundStyle(.secondary).textSelection(.enabled)
                if !model.releaseDetail.isEmpty {
                    Text(model.releaseDetail).font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
                HStack(spacing: 14) {
                    Label("Lossless", systemImage: "waveform")
                    Text("16-bit / 44.1 kHz")
                    if !model.tracks.isEmpty { Text("\(model.tracks.count) tracks") }
                }.font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                if !model.tracks.isEmpty {
                    HStack(spacing: 14) {
                        Label(model.cover == nil ? "Finding artwork" : "Artwork saved",
                              systemImage: model.cover == nil ? "photo" : "checkmark.circle.fill")
                        Label("\(model.tracks.filter { $0.lyrics }.count) with lyrics", systemImage: "text.quote")
                    }.font(.caption).foregroundStyle(.secondary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }.padding(28)
    }

    private var trackTable: some View {
        Table(model.tracks) {
            TableColumn("#") { item in
                Text("\(item.id)").monospacedDigit().foregroundStyle(.secondary)
            }.width(28)
            TableColumn("Title") { item in
                Text(item.title).fontWeight(item.id == model.track && model.partial != nil ? .semibold : .regular)
                    .lineLimit(1).help(item.title)
            }
            TableColumn("Time") { item in
                Text(item.duration).monospacedDigit().foregroundStyle(.secondary)
            }.width(48)
            TableColumn("Status") { item in
                if item.imported {
                    Label("In Music", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                } else if item.saved {
                    Label("Ripped", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                } else if item.id == model.track && model.partial != nil {
                    HStack(spacing: 7) {
                        ProgressView(value: min(1, max(0, model.fraction * Double(model.total) - Double(item.id - 1))))
                            .frame(width: 42)
                        Text("Ripping").foregroundStyle(Color.accentColor)
                    }.accessibilityLabel("Ripping track \(item.id)")
                } else {
                    Text("Waiting").foregroundStyle(.tertiary)
                }
            }.width(112)
            TableColumn("Lyrics") { item in
                Image(systemName: item.lyrics ? "text.quote" : "minus")
                    .foregroundStyle(item.lyrics ? Color.secondary : Color.secondary.opacity(0.4))
                    .accessibilityLabel(item.lyrics ? "Lyrics saved" : "No lyrics saved")
            }.width(42)
        }.tableStyle(.inset(alternatesRowBackgrounds: true))
    }

    private var progressFooter: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(alignment: .firstTextBaseline) {
                Label(model.stopping ? "Stopping after this CD" : model.phase,
                      systemImage: model.phase == "Needs attention" ? "exclamationmark.triangle.fill" :
                        (model.audioComplete ? "checkmark.circle.fill" : "opticaldisc"))
                    .font(.headline)
                    .foregroundStyle(model.phase == "Needs attention" ? Color.orange : Color.primary)
                Spacer()
                if model.total > 0 {
                    Text("\(model.tracks.filter { $0.saved }.count) of \(model.total) tracks ripped")
                        .font(.callout).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            if model.total > 0 {
                ProgressView(value: model.fraction).accessibilityLabel("Audio extraction progress")
            }
            Text(model.detail).font(.callout).foregroundStyle(.secondary)
                .lineLimit(3).textSelection(.enabled)
            HStack(spacing: 10) {
                if model.running {
                    Button(model.stopping ? "Finishing…" : "Stop After This CD") { model.stop() }
                        .disabled(model.stopping)
                } else {
                    Button("Start Watching") { model.launch("watch") }
                        .buttonStyle(.borderedProminent).disabled(model.checking)
                    Button("Rip Inserted CD") { model.launch("rip") }.disabled(model.checking)
                }
                Spacer()
                Button("Show in Finder") {
                    if let folder = model.folder { NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: folder.path) }
                    else { model.openLibrary() }
                }.disabled(model.folder == nil)
            }.controlSize(.large)
        }.padding(24)
    }

    private var editionPicker: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose Your Edition").font(.title3.bold())
            Text("Match the date, country and catalogue number on your CD.")
                .foregroundStyle(.secondary)
            ScrollView {
                VStack(spacing: 8) {
                    ForEach(model.editions) { edition in
                        Button { model.choose(edition.id) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(edition.title).fontWeight(.semibold)
                                    Text(edition.detail).font(.callout).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.secondary)
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        }.buttonStyle(.bordered)
                    }
                }
            }.frame(minHeight: 130)
            Button("Identify Later") { model.choose(nil) }
        }.padding(24).frame(maxHeight: .infinity)
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text("Settings").font(.title2.bold())
            GroupBox {
                VStack(alignment: .leading, spacing: 16) {
                    Toggle("Add finished albums to Music", isOn: $model.addToMusic)
                    Toggle("Fetch available lyrics", isOn: $model.fetchLyrics)
                    Divider()
                    Text("Archive folder").font(.headline)
                    HStack {
                        Text(model.output.path).font(.caption).textSelection(.enabled)
                        Spacer()
                        Button("Choose…") { model.chooseFolder() }
                    }
                }.padding(12)
            }.disabled(model.running || model.checking)
            Text("In Music settings, set CD insertion to Show CD so only one app controls the drive.")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Check Setup") { model.launch("doctor") }.disabled(model.running || model.checking)
                Spacer()
                Button("Done") { model.showSettings = false }.keyboardShortcut(.defaultAction)
            }
        }.padding(26).frame(width: 460)
    }

    private var activity: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Activity").font(.title2.bold())
            ScrollView {
                Text(model.logs.isEmpty ? "No activity this session." : model.logs.joined(separator: "\n"))
                    .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.frame(height: 320)
            HStack { Spacer(); Button("Done") { model.showLog = false }.keyboardShortcut(.defaultAction) }
        }.padding(26).frame(width: 600)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSToolbarDelegate {
    let model = Ripper()
    var item: NSStatusItem!
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "opticaldisc", accessibilityDescription: "CD Rip")
        item.button?.target = self
        item.button?.action = #selector(toggleWindow)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 790),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "CD Rip"
        window.minSize = NSSize(width: 720, height: 680)
        window.setFrameAutosaveName("CDRipMainWindowV2")
        let toolbar = NSToolbar(identifier: "CDRipToolbar")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        window.toolbar = toolbar
        window.toolbarStyle = .unified
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = NSHostingView(rootView: ContentView(model: model))
        window.center()
        let menu = NSMenu()
        let root = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Show CD Rip", action: #selector(showWindow), keyEquivalent: "0")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit CD Rip", action: #selector(requestQuit), keyEquivalent: "q")
        root.submenu = appMenu
        menu.addItem(root)
        NSApp.mainMenu = menu
        showWindow()
        DispatchQueue.main.async { self.model.restoreLatestAlbum() }
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, NSToolbarItem.Identifier("archive"), NSToolbarItem.Identifier("activity"), NSToolbarItem.Identifier("settings")]
    }
    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        toolbarAllowedItemIdentifiers(toolbar)
    }
    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: identifier)
        let values: (String, String, Selector)
        switch identifier.rawValue {
        case "archive": values = ("Open Archive", "folder", #selector(openArchive))
        case "activity": values = ("Activity", "list.bullet.rectangle", #selector(showActivity))
        case "settings": values = ("Settings", "gearshape", #selector(showSettings))
        default: return nil
        }
        item.label = values.0
        item.toolTip = values.0
        item.image = NSImage(systemSymbolName: values.1, accessibilityDescription: values.0)
        item.target = self
        item.action = values.2
        return item
    }
    @objc func openArchive() { model.openLibrary() }
    @objc func showActivity() { model.showLog = true }
    @objc func showSettings() { model.showSettings = true }

    @objc func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func toggleWindow() {
        if window.isVisible { window.orderOut(nil) } else { showWindow() }
    }
    @objc func requestQuit() {
        if model.running {
            model.quitWhenDone = true
            model.stop()
        } else { NSApp.terminate(nil) }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if model.running {
            model.quitWhenDone = true
            model.stop()
            return .terminateCancel
        }
        return .terminateNow
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
