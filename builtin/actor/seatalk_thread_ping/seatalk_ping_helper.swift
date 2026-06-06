import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

let targetChatName = "Radar"
let mentionMarker = "@You"
let maxChildrenPerNode = 120
let defaultThreadIconRightOffset = 239.0
let defaultThreadIconTopOffset = 31.0
let defaultThreadRowRightOffset = 320.0
let defaultThreadRowTopOffset = 160.0
let defaultThreadOpenDelay = 0.8

struct ElementMatch {
    let element: AXUIElement
    let texts: [String]
    let depth: Int
    let frame: CGRect?
}

func emit(_ object: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(0)
}

func emitFailure(_ message: String, extra: [String: Any] = [:]) -> Never {
    var payload = extra
    payload["success"] = false
    payload["available"] = false
    payload["error"] = message
    emit(payload)
}

func envDouble(_ name: String, defaultValue: Double) -> Double {
    guard let value = ProcessInfo.processInfo.environment[name],
          let number = Double(value) else {
        return defaultValue
    }
    return number
}

func textAttribute(_ element: AXUIElement, _ attribute: CFString) -> String? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    guard error == .success, let value else {
        return nil
    }
    if let string = value as? String {
        let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
    if let attributed = value as? NSAttributedString {
        let trimmed = attributed.string.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
    if let number = value as? NSNumber {
        return number.stringValue
    }
    return nil
}

func children(of element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    guard error == .success, let children = value as? [AXUIElement] else {
        return []
    }
    if children.count <= maxChildrenPerNode {
        return children
    }
    return Array(children.prefix(maxChildrenPerNode))
}

func frame(of element: AXUIElement) -> CGRect? {
    var positionValue: CFTypeRef?
    var sizeValue: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &positionValue) == .success,
          AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &sizeValue) == .success,
          let positionAX = positionValue,
          let sizeAX = sizeValue else {
        return nil
    }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(positionAX as! AXValue, .cgPoint, &point),
          AXValueGetValue(sizeAX as! AXValue, .cgSize, &size) else {
        return nil
    }
    return CGRect(origin: point, size: size)
}

func ownTexts(_ element: AXUIElement) -> [String] {
    var values: [String] = []
    for attribute in [
        kAXRoleAttribute,
        kAXSubroleAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
        kAXDescriptionAttribute,
        kAXHelpAttribute,
        kAXIdentifierAttribute,
    ] {
        if let value = textAttribute(element, attribute as CFString), !values.contains(value) {
            values.append(value)
        }
    }
    return values
}

func actionNames(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    let error = AXUIElementCopyActionNames(element, &names)
    guard error == .success, let names else {
        return []
    }
    return (names as [AnyObject]).compactMap { $0 as? String }
}

func subtreeTexts(_ element: AXUIElement, maxDepth: Int) -> [String] {
    var values = ownTexts(element)
    if maxDepth <= 0 {
        return values
    }
    for child in children(of: element) {
        for value in subtreeTexts(child, maxDepth: maxDepth - 1) where !values.contains(value) {
            values.append(value)
        }
    }
    return values
}

func containsText(_ texts: [String], _ needle: String) -> Bool {
    let lowered = needle.lowercased()
    return texts.contains { $0.lowercased().contains(lowered) }
}

func findElements(
    _ element: AXUIElement,
    maxDepth: Int,
    depth: Int = 0,
    predicate: (AXUIElement, [String], Int) -> Bool
) -> [ElementMatch] {
    let texts = subtreeTexts(element, maxDepth: 2)
    var matches: [ElementMatch] = []
    if predicate(element, texts, depth) {
        matches.append(ElementMatch(element: element, texts: texts, depth: depth, frame: frame(of: element)))
    }
    if depth >= maxDepth {
        return matches
    }
    for child in children(of: element) {
        matches.append(contentsOf: findElements(child, maxDepth: maxDepth, depth: depth + 1, predicate: predicate))
    }
    return matches
}

func findOwnTextElements(
    _ element: AXUIElement,
    needle: String,
    maxDepth: Int,
    depth: Int = 0
) -> [ElementMatch] {
    let texts = ownTexts(element)
    var matches: [ElementMatch] = []
    if containsText(texts, needle) {
        matches.append(ElementMatch(element: element, texts: texts, depth: depth, frame: frame(of: element)))
    }
    if depth >= maxDepth {
        return matches
    }
    for child in children(of: element) {
        matches.append(contentsOf: findOwnTextElements(child, needle: needle, maxDepth: maxDepth, depth: depth + 1))
    }
    return matches
}

func seaTalkApp() -> NSRunningApplication? {
    NSWorkspace.shared.runningApplications.first { app in
        let name = (app.localizedName ?? "").lowercased()
        let bundle = (app.bundleIdentifier ?? "").lowercased()
        return name == "seatalk" || bundle == "com.seagroup.seatalkmac.enterprise"
    }
}

func appElement(_ app: NSRunningApplication) -> AXUIElement {
    AXUIElementCreateApplication(app.processIdentifier)
}

func focusedWindow(_ root: AXUIElement) -> AXUIElement? {
    var value: CFTypeRef?
    if AXUIElementCopyAttributeValue(root, kAXFocusedWindowAttribute as CFString, &value) == .success,
       let value {
        return unsafeBitCast(value, to: AXUIElement.self)
    }
    return children(of: root).first
}

func press(_ element: AXUIElement) -> Bool {
    if AXUIElementPerformAction(element, kAXPressAction as CFString) == .success {
        return true
    }
    guard let rect = frame(of: element), rect.width > 0, rect.height > 0 else {
        return false
    }
    let point = CGPoint(x: rect.midX, y: rect.midY)
    guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else {
        return false
    }
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    return true
}

func click(point: CGPoint) -> Bool {
    guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else {
        return false
    }
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    return true
}

func findRadarMentionRow(in root: AXUIElement) -> ElementMatch? {
    let windowFrame = focusedWindow(root).flatMap(frame(of:))
    let sidebarMaxX = windowFrame.map { $0.minX + ($0.width * 0.36) } ?? 620
    let radarLabels = findOwnTextElements(root, needle: targetChatName, maxDepth: 34)
    let mentionLabels = findOwnTextElements(root, needle: mentionMarker, maxDepth: 34)
    for mention in mentionLabels {
        guard let mentionFrame = mention.frame, mentionFrame.midX < sidebarMaxX else {
            continue
        }
        if let radar = radarLabels.first(where: { radar in
            guard let radarFrame = radar.frame else {
                return false
            }
            return radarFrame.midX < sidebarMaxX && abs(radarFrame.midY - mentionFrame.midY) <= 42
        }) {
            return ElementMatch(
                element: mention.element,
                texts: radar.texts + mention.texts,
                depth: mention.depth,
                frame: mention.frame
            )
        }
    }

    let matches = findElements(root, maxDepth: 32) { _, texts, _ in
        containsText(texts, targetChatName) && containsText(texts, mentionMarker)
    }
    return matches.sorted { lhs, rhs in
        let lhsArea = lhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        let rhsArea = rhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        if abs(lhsArea - rhsArea) > 1 {
            return lhsArea < rhsArea
        }
        return lhs.depth > rhs.depth
    }.first
}

func findMentionInConversation(in root: AXUIElement, windowFrame: CGRect?) -> ElementMatch? {
    let minX = windowFrame.map { $0.minX + ($0.width * 0.32) } ?? 0
    let matches = findOwnTextElements(root, needle: mentionMarker, maxDepth: 34).filter { match in
        guard let rect = match.frame else {
            return true
        }
        return rect.midX >= minX && rect.width > 0 && rect.height > 0
    }
    return matches.sorted { lhs, rhs in
        let lhsArea = lhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        let rhsArea = rhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        if abs(lhsArea - rhsArea) > 1 {
            return lhsArea < rhsArea
        }
        return lhs.depth > rhs.depth
    }.first
}

func findThreadDrawerMention(in root: AXUIElement, windowFrame: CGRect?) -> ElementMatch? {
    let minX = windowFrame.map { $0.minX + ($0.width * 0.62) } ?? 1050
    let matches = findOwnTextElements(root, needle: mentionMarker, maxDepth: 34).filter { match in
        guard let rect = match.frame else {
            return false
        }
        return rect.midX >= minX && rect.width >= 8 && rect.height >= 8
    }
    return matches.sorted { lhs, rhs in
        let lhsY = lhs.frame?.minY ?? CGFloat.greatestFiniteMagnitude
        let rhsY = rhs.frame?.minY ?? CGFloat.greatestFiniteMagnitude
        return lhsY < rhsY
    }.first
}

func findThreadDrawerOpener(in root: AXUIElement, windowFrame: CGRect?) -> ElementMatch? {
    let minX = windowFrame.map { $0.minX + ($0.width * 0.32) } ?? 0
    let maxX = windowFrame.map { $0.minX + ($0.width * 0.62) } ?? 1050
    let matches = findOwnTextElements(root, needle: mentionMarker, maxDepth: 34).filter { match in
        guard let rect = match.frame else {
            return false
        }
        return rect.midX >= minX && rect.midX < maxX && rect.width > 0 && rect.height > 0
    }
    return matches.sorted { lhs, rhs in
        let lhsArea = lhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        let rhsArea = rhs.frame.map { $0.width * $0.height } ?? CGFloat.greatestFiniteMagnitude
        if abs(lhsArea - rhsArea) > 1 {
            return lhsArea < rhsArea
        }
        return lhs.depth > rhs.depth
    }.first
}

func clickThreadDrawerMention(_ mention: ElementMatch) -> Bool {
    guard let rect = mention.frame else {
        return press(mention.element)
    }
    // Click inside the thread row, slightly to the right of the red @You label.
    return click(point: CGPoint(x: rect.minX + 120, y: rect.midY))
}

func sampleTexts(_ texts: [String], limit: Int = 8) -> [String] {
    Array(texts.prefix(limit))
}

func detect() -> Never {
    guard AXIsProcessTrusted() else {
        emitFailure("macOS Accessibility permission is not granted")
    }
    guard let app = seaTalkApp() else {
        emit([
            "success": true,
            "available": false,
            "reason": "SeaTalk is not running",
        ])
    }
    let frontmost = NSWorkspace.shared.frontmostApplication
    let isFrontmost = frontmost?.processIdentifier == app.processIdentifier
    guard isFrontmost else {
        emit([
            "success": true,
            "available": false,
            "reason": "SeaTalk is not frontmost",
            "chat_name": targetChatName,
        ])
    }
    let root = appElement(app)
    guard let row = findRadarMentionRow(in: root) else {
        emit([
            "success": true,
            "available": false,
            "reason": "Radar chat does not show an @You mention",
            "chat_name": targetChatName,
        ])
    }
    emit([
        "success": true,
        "available": true,
        "reason": "Radar chat shows an @You mention",
        "chat_name": targetChatName,
        "marker": mentionMarker,
        "matched_texts": sampleTexts(row.texts),
    ])
}

func detectConversationMention() -> Never {
    guard AXIsProcessTrusted() else {
        emitFailure("macOS Accessibility permission is not granted")
    }
    guard let app = seaTalkApp() else {
        emit([
            "success": true,
            "available": false,
            "reason": "SeaTalk is not running",
        ])
    }
    let root = appElement(app)
    let window = focusedWindow(root)
    let windowFrame = window.flatMap(frame(of:))
    let mention = findThreadDrawerMention(in: window ?? root, windowFrame: windowFrame)
        ?? findThreadDrawerOpener(in: window ?? root, windowFrame: windowFrame)
    guard let mention else {
        emit([
            "success": true,
            "available": false,
            "reason": "Current SeaTalk conversation does not show an @You thread marker",
            "chat_name": targetChatName,
        ])
    }
    emit([
        "success": true,
        "available": true,
        "reason": "Current SeaTalk conversation shows a clickable @You marker",
        "chat_name": targetChatName,
        "marker": mentionMarker,
        "matched_texts": sampleTexts(mention.texts),
    ])
}

func promptPermissions() -> Never {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    let trusted = AXIsProcessTrustedWithOptions(options)
    emit([
        "success": trusted,
        "available": false,
        "trusted": trusted,
        "message": trusted
            ? "macOS Accessibility permission is granted"
            : "macOS Accessibility permission prompt requested",
    ])
}

func openPingThread() -> Never {
    guard AXIsProcessTrusted() else {
        emitFailure("macOS Accessibility permission is not granted")
    }
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    app.activate(options: [.activateIgnoringOtherApps])
    Thread.sleep(forTimeInterval: 0.3)

    var root = appElement(app)
    var window = focusedWindow(root)
    var windowFrame = window.flatMap(frame(of:))
    guard let initialWindowFrame = windowFrame else {
        emitFailure("Could not read the SeaTalk window frame")
    }

    if let drawerMention = findThreadDrawerMention(in: window ?? root, windowFrame: windowFrame) {
        guard clickThreadDrawerMention(drawerMention) else {
            emitFailure("Could not click the @You thread row in My Threads", extra: ["matched_texts": sampleTexts(drawerMention.texts)])
        }
        emit([
            "success": true,
            "message": "Selected the Radar @You thread from My Threads",
            "chat_name": targetChatName,
            "marker": mentionMarker,
            "matched_texts": sampleTexts(drawerMention.texts),
        ])
    }

    let iconRightOffset = envDouble("SEATALK_THREAD_ICON_RIGHT", defaultValue: defaultThreadIconRightOffset)
    let iconTopOffset = envDouble("SEATALK_THREAD_ICON_TOP", defaultValue: defaultThreadIconTopOffset)
    let rowRightOffset = envDouble("SEATALK_THREAD_ROW_RIGHT", defaultValue: defaultThreadRowRightOffset)
    let rowTopOffset = envDouble("SEATALK_THREAD_ROW_TOP", defaultValue: defaultThreadRowTopOffset)
    let openDelay = envDouble("SEATALK_THREAD_OPEN_DELAY", defaultValue: defaultThreadOpenDelay)

    let iconPoint = CGPoint(
        x: initialWindowFrame.maxX - iconRightOffset,
        y: initialWindowFrame.minY + iconTopOffset
    )
    guard click(point: iconPoint) else {
        emitFailure("Could not click the SeaTalk My Threads toolbar icon")
    }
    Thread.sleep(forTimeInterval: openDelay)

    root = appElement(app)
    window = focusedWindow(root)
    windowFrame = window.flatMap(frame(of:))
    if let drawerMention = findThreadDrawerMention(in: window ?? root, windowFrame: windowFrame) {
        guard clickThreadDrawerMention(drawerMention) else {
            emitFailure("Could not click the @You thread row in My Threads", extra: ["matched_texts": sampleTexts(drawerMention.texts)])
        }
        emit([
            "success": true,
            "message": "Selected the Radar @You thread from My Threads",
            "chat_name": targetChatName,
            "marker": mentionMarker,
            "matched_texts": sampleTexts(drawerMention.texts),
        ])
    }

    let fallbackWindowFrame = windowFrame ?? initialWindowFrame
    let rowPoint = CGPoint(
        x: fallbackWindowFrame.maxX - rowRightOffset,
        y: fallbackWindowFrame.minY + rowTopOffset
    )
    guard click(point: rowPoint) else {
        emitFailure("Could not click the calibrated @You thread row in My Threads")
    }
    emit([
        "success": true,
        "message": "Clicked the calibrated Radar @You thread row",
        "chat_name": targetChatName,
        "marker": mentionMarker,
        "click_points": [
            "thread_icon": ["x": Int(iconPoint.x), "y": Int(iconPoint.y)],
            "thread_row": ["x": Int(rowPoint.x), "y": Int(rowPoint.y)],
        ],
    ])
}

func dumpTree() -> Never {
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    let root = appElement(app)
    var lines: [String] = []
    func visit(_ element: AXUIElement, depth: Int, path: String) {
        let texts = ownTexts(element)
        let label = texts.joined(separator: " | ")
        let rect = frame(of: element).map { " [\(Int($0.minX)),\(Int($0.minY)) \(Int($0.width))x\(Int($0.height))]" } ?? ""
        lines.append(String(repeating: "  ", count: depth) + "\(path) " + label + rect)
        if depth >= 12 {
            return
        }
        for (index, child) in children(of: element).enumerated() {
            visit(child, depth: depth + 1, path: "\(path).\(index + 1)")
        }
    }
    visit(root, depth: 0, path: "1")
    emit([
        "success": true,
        "app_name": app.localizedName ?? "SeaTalk",
        "bundle_id": app.bundleIdentifier ?? "",
        "lines": lines,
    ])
}

func scanTree() -> Never {
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    let root = appElement(app)
    var matches: [[String: Any]] = []
    func visit(_ element: AXUIElement, depth: Int, path: String) {
        let texts = ownTexts(element)
        if texts.contains(where: { text in
            text.localizedCaseInsensitiveContains(targetChatName)
                || text.localizedCaseInsensitiveContains(mentionMarker)
        }) {
            var item: [String: Any] = [
                "path": path,
                "depth": depth,
                "texts": texts,
            ]
            if let rect = frame(of: element) {
                item["frame"] = [
                    "x": Int(rect.minX),
                    "y": Int(rect.minY),
                    "width": Int(rect.width),
                    "height": Int(rect.height),
                ]
            }
            matches.append(item)
        }
        if depth >= 30 {
            return
        }
        for (index, child) in children(of: element).enumerated() {
            visit(child, depth: depth + 1, path: "\(path).\(index + 1)")
        }
    }
    visit(root, depth: 0, path: "1")
    emit([
        "success": true,
        "matches": matches,
    ])
}

func textTree() -> Never {
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    let root = appElement(app)
    var nodes: [[String: Any]] = []
    func visit(_ element: AXUIElement, depth: Int, path: String) {
        let texts = ownTexts(element)
        if !texts.isEmpty {
            var item: [String: Any] = [
                "path": path,
                "depth": depth,
                "texts": texts,
            ]
            if let rect = frame(of: element) {
                item["frame"] = [
                    "x": Int(rect.minX),
                    "y": Int(rect.minY),
                    "width": Int(rect.width),
                    "height": Int(rect.height),
                ]
            }
            nodes.append(item)
        }
        if depth >= 34 {
            return
        }
        for (index, child) in children(of: element).enumerated() {
            visit(child, depth: depth + 1, path: "\(path).\(index + 1)")
        }
    }
    visit(root, depth: 0, path: "1")
    emit([
        "success": true,
        "nodes": nodes,
    ])
}

func toolbarTree() -> Never {
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    let root = appElement(app)
    var nodes: [[String: Any]] = []
    func visit(_ element: AXUIElement, depth: Int, path: String) {
        let texts = ownTexts(element)
        let actions = actionNames(element)
        var include = false
        var item: [String: Any] = [
            "path": path,
            "depth": depth,
            "texts": texts,
            "actions": actions,
        ]
        if let rect = frame(of: element) {
            item["frame"] = [
                "x": Int(rect.minX),
                "y": Int(rect.minY),
                "width": Int(rect.width),
                "height": Int(rect.height),
            ]
            include = rect.minX >= 1200 && rect.minY <= 110 && rect.width > 0 && rect.height > 0
        }
        if include || !actions.isEmpty {
            nodes.append(item)
        }
        if depth >= 28 {
            return
        }
        for (index, child) in children(of: element).enumerated() {
            visit(child, depth: depth + 1, path: "\(path).\(index + 1)")
        }
    }
    visit(root, depth: 0, path: "1")
    emit([
        "success": true,
        "nodes": nodes,
    ])
}

func hoverPoint() -> Never {
    guard CommandLine.arguments.count >= 4,
          let x = Double(CommandLine.arguments[2]),
          let y = Double(CommandLine.arguments[3]) else {
        emitFailure("usage: hover-point <x> <y>")
    }
    let point = CGPoint(x: x, y: y)
    CGWarpMouseCursorPosition(point)
    if let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left) {
        move.post(tap: .cghidEventTap)
    }
    Thread.sleep(forTimeInterval: 0.9)
    guard let app = seaTalkApp() else {
        emitFailure("SeaTalk is not running")
    }
    let root = appElement(app)
    let matches = findElements(root, maxDepth: 24) { _, texts, _ in
        containsText(texts, "threads") || containsText(texts, "My")
    }
    let nodes = matches.map { match -> [String: Any] in
        var item: [String: Any] = [
            "depth": match.depth,
            "texts": match.texts,
        ]
        if let rect = match.frame {
            item["frame"] = [
                "x": Int(rect.minX),
                "y": Int(rect.minY),
                "width": Int(rect.width),
                "height": Int(rect.height),
            ]
        }
        return item
    }
    emit([
        "success": true,
        "x": x,
        "y": y,
        "matches": nodes,
    ])
}

func clickPointCommand() -> Never {
    guard CommandLine.arguments.count >= 4,
          let x = Double(CommandLine.arguments[2]),
          let y = Double(CommandLine.arguments[3]) else {
        emitFailure("usage: click-point <x> <y>")
    }
    let point = CGPoint(x: x, y: y)
    guard click(point: point) else {
        emitFailure("Could not click point")
    }
    Thread.sleep(forTimeInterval: 0.4)
    emit([
        "success": true,
        "x": x,
        "y": y,
    ])
}

func listApps() -> Never {
    let apps = NSWorkspace.shared.runningApplications.compactMap { app -> [String: Any]? in
        let name = app.localizedName ?? ""
        let bundle = app.bundleIdentifier ?? ""
        if name.lowercased().contains("sea") || bundle.lowercased().contains("sea") {
            return [
                "name": name,
                "bundle_id": bundle,
                "pid": app.processIdentifier,
            ]
        }
        return nil
    }
    emit([
        "success": true,
        "apps": apps,
    ])
}

let command = CommandLine.arguments.dropFirst().first ?? "detect"
switch command {
case "detect":
    detect()
case "detect-conversation":
    detectConversationMention()
case "open":
    openPingThread()
case "dump":
    dumpTree()
case "prompt":
    promptPermissions()
case "list":
    listApps()
case "scan":
    scanTree()
case "text":
    textTree()
case "toolbar":
    toolbarTree()
case "hover-point":
    hoverPoint()
case "click-point":
    clickPointCommand()
default:
    emitFailure("unknown command: \(command)")
}
