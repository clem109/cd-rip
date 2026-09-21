import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        let transform = NSAffineTransform()
        transform.scale(by: Double(pixels) / 1024)
        transform.concat()
        let background = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 205, yRadius: 205)
        NSGradient(starting: NSColor(calibratedRed: 1, green: 0.82, blue: 0.56, alpha: 1),
                   ending: NSColor(calibratedRed: 0.92, green: 0.36, blue: 0.16, alpha: 1))!.draw(in: background, angle: -70)
        let disc = NSBezierPath(ovalIn: NSRect(x: 202, y: 202, width: 620, height: 620))
        NSGradient(starting: .white, ending: NSColor(calibratedWhite: 0.83, alpha: 1))!.draw(in: disc, angle: -40)
        NSColor(calibratedWhite: 0.68, alpha: 0.35).setStroke()
        for radius in [240.0, 266.0, 286.0] {
            let ring = NSBezierPath(ovalIn: NSRect(x: 512-radius, y: 512-radius, width: radius*2, height: radius*2))
            ring.lineWidth = 2
            ring.stroke()
        }
        NSColor(calibratedWhite: 0.7, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 422, y: 422, width: 180, height: 180)).fill()
        NSColor(calibratedRed: 0.94, green: 0.49, blue: 0.24, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 452, y: 452, width: 120, height: 120)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let url = output.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
        try rep.representation(using: .png, properties: [:])!.write(to: url)
    }
}
