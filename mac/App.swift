import AppKit
import SwiftUI

struct Edition: Identifiable {
    let id: Int
    let title: String
    let detail: String
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
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Image(systemName: "opticaldisc.fill").font(.title2).foregroundStyle(.orange)
                Text("CD Rip").font(.title2.bold())
                Spacer()
                Text("APPLE LOSSLESS").font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 18) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14).fill(.quaternary)
                    if let image = model.cover {
                        Image(nsImage: image).resizable().scaledToFill()
                    } else {
                        Image(systemName: "opticaldisc").font(.system(size: 48, weight: .ultraLight))
                            .foregroundStyle(.secondary)
                    }
                }.frame(width: 96, height: 96).clipShape(RoundedRectangle(cornerRadius: 14))
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.album).font(.title3.bold()).lineLimit(3)
                    Text(model.artist).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 7) {
                    Circle().fill(model.running ? Color.green : Color.secondary.opacity(0.4)).frame(width: 6, height: 6)
                    Text(model.stopping ? "Stopping after this CD" : model.phase).font(.headline)
                }
                if model.total > 0 { ProgressView(value: model.fraction).tint(.orange) }
                Text(model.detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if !model.editions.isEmpty {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(model.editions) { edition in
                            Button { model.choose(edition.id) } label: {
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(edition.title).fontWeight(.medium)
                                        Text(edition.detail).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                }.padding(8).frame(maxWidth: .infinity)
                            }.buttonStyle(.bordered)
                        }
                    }
                }.frame(height: 180)
                Button("Identify later") { model.choose(nil) }.buttonStyle(.link)
            }
            HStack {
                if model.running {
                    Button(model.stopping ? "Finishing…" : "Stop after this CD") { model.stop() }
                        .disabled(model.stopping).buttonStyle(.borderedProminent).tint(.orange)
                } else {
                    Button("Start watching") { model.launch("watch") }
                        .buttonStyle(.borderedProminent).tint(.orange).disabled(model.checking)
                    Button("Rip inserted CD") { model.launch("rip") }.disabled(model.checking)
                }
                Spacer()
            }
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                Toggle("Add finished albums to Music", isOn: $model.addToMusic)
                Toggle("Fetch available lyrics", isOn: $model.fetchLyrics)
                HStack {
                    Image(systemName: "folder").foregroundStyle(.secondary)
                    Text(model.output.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                        .font(.caption).lineLimit(1).truncationMode(.middle)
                    Spacer()
                    Button("Change…") { model.chooseFolder() }
                }
            }.disabled(model.running || model.checking)
            HStack {
                Button("Open library") { model.openLibrary() }
                Button("Check setup") { model.launch("doctor") }.disabled(model.running || model.checking)
                Spacer()
                Button { model.showLog.toggle() } label: { Image(systemName: "text.alignleft") }
                    .help("Activity log")
            }
            if model.showLog {
                ScrollView {
                    Text(model.logs.joined(separator: "\n")).font(.system(size: 10, design: .monospaced))
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }.frame(height: 120).padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            Text("Set Music’s CD insertion setting to Show CD before starting.")
                .font(.caption2).foregroundStyle(.secondary)
        }.padding(24).frame(width: 440)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = Ripper()
    var item: NSStatusItem!
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "opticaldisc", accessibilityDescription: "CD Rip")
        item.button?.target = self
        item.button?.action = #selector(toggleWindow)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 440, height: 535),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "CD Rip"
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
    }

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
