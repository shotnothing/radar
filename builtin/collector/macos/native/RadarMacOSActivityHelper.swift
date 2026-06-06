import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

let promptPermissions = CommandLine.arguments.contains("--prompt-permissions")
let outputQueue = DispatchQueue(label: "radar.macos.activity.output")
let captureQueue = DispatchQueue(label: "radar.macos.activity.capture")

func epochMs() -> Int64 {
    Int64(Date().timeIntervalSince1970 * 1000)
}

func writeJSON(_ object: [String: Any]) {
    outputQueue.async {
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: []),
              let line = String(data: data, encoding: .utf8) else {
            return
        }
        FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
    }
}

func isSupportedTerminalContext(_ context: [String: Any]) -> Bool {
    let bundleID = (context["bundle_id"] as? String ?? "").lowercased()
    let appName = (context["app_name"] as? String ?? "").lowercased()
    let supportedBundleIDs: Set<String> = [
        "com.apple.terminal",
        "com.googlecode.iterm2",
        "dev.warp.warp-stable",
        "io.alacritty",
        "com.github.wez.wezterm",
        "net.kovidgoyal.kitty",
        "co.zeit.hyper",
        "com.microsoft.vscode",
        "com.jetbrains.intellij.ce",
        "com.jetbrains.intellij",
        "com.jetbrains.goland",
        "com.jetbrains.pycharm",
        "com.jetbrains.pycharm.ce",
        "com.jetbrains.webstorm",
        "com.todesktop.230313mzl4w4u92"
    ]
    let supportedAppNames: Set<String> = [
        "terminal",
        "iterm",
        "iterm2",
        "warp",
        "alacritty",
        "wezterm",
        "kitty",
        "hyper",
        "visual studio code",
        "code",
        "cursor",
        "intellij idea",
        "goland",
        "pycharm",
        "webstorm"
    ]
    return supportedBundleIDs.contains(bundleID) || supportedAppNames.contains(appName)
}

func characters(from event: CGEvent) -> String? {
    var length = 0
    var chars = [UniChar](repeating: 0, count: 8)
    event.keyboardGetUnicodeString(
        maxStringLength: chars.count,
        actualStringLength: &length,
        unicodeString: &chars
    )
    if length <= 0 {
        return nil
    }
    return String(utf16CodeUnits: chars, count: length)
}

func stringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    guard error == .success, let value else {
        return nil
    }
    if let string = value as? String {
        let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
    if let url = value as? URL {
        return url.absoluteString
    }
    return nil
}

func foregroundContext() -> [String: Any] {
    var context: [String: Any] = [:]
    guard let activeApp = NSWorkspace.shared.frontmostApplication else {
        return context
    }

    if let appName = activeApp.localizedName?.trimmingCharacters(in: .whitespacesAndNewlines), !appName.isEmpty {
        context["app_name"] = appName
    }
    if let bundleID = activeApp.bundleIdentifier?.trimmingCharacters(in: .whitespacesAndNewlines), !bundleID.isEmpty {
        context["bundle_id"] = bundleID
    }

    let appElement = AXUIElementCreateApplication(activeApp.processIdentifier)

    var windowValue: CFTypeRef?
    if AXUIElementCopyAttributeValue(appElement, kAXFocusedWindowAttribute as CFString, &windowValue) == .success,
       let windowValue {
        let windowElement = unsafeBitCast(windowValue, to: AXUIElement.self)
        if let title = stringAttribute(windowElement, kAXTitleAttribute as CFString) {
            context["window_title"] = title
        }
        if let document = stringAttribute(windowElement, kAXDocumentAttribute as CFString) {
            context["document_path"] = document
        }
        if let url = stringAttribute(windowElement, kAXURLAttribute as CFString) {
            context["url"] = url
        }
    }

    var focusedValue: CFTypeRef?
    if AXUIElementCopyAttributeValue(appElement, kAXFocusedUIElementAttribute as CFString, &focusedValue) == .success,
       let focusedValue {
        let focusedElement = unsafeBitCast(focusedValue, to: AXUIElement.self)
        var focused: [String: Any] = [:]
        if let title = stringAttribute(focusedElement, kAXTitleAttribute as CFString) {
            focused["title"] = title
        }
        if let value = stringAttribute(focusedElement, kAXValueAttribute as CFString) {
            focused["value"] = value
        }
        if let document = stringAttribute(focusedElement, kAXDocumentAttribute as CFString) {
            context["document_path"] = document
        }
        if let url = stringAttribute(focusedElement, kAXURLAttribute as CFString) {
            context["url"] = url
        }
        if let role = stringAttribute(focusedElement, kAXRoleAttribute as CFString) {
            focused["role"] = role
        }
        if let description = stringAttribute(focusedElement, kAXDescriptionAttribute as CFString) {
            focused["description"] = description
        }
        if let selectedText = stringAttribute(focusedElement, kAXSelectedTextAttribute as CFString) {
            focused["selected_text"] = selectedText
        }
        if !focused.isEmpty {
            context["focused_element"] = focused
        }
    }

    return context
}

func emitStatus(_ status: String, error: String? = nil) {
    let trusted = AXIsProcessTrusted()
    var payload: [String: Any] = [
        "type": "status",
        "status": status,
        "timestamp": epochMs(),
        "permissions": [
            "macos_accessibility": trusted,
            "macos_input_monitoring": "unknown"
        ]
    ]
    if let error {
        payload["error"] = error
    }
    writeJSON(payload)
}

func emitEvent(name: String, fields: [String: Any]) {
    let observedAt = epochMs()
    captureQueue.async {
        var payload: [String: Any] = [
            "type": "event",
            "event_name": name,
            "observed_at": observedAt,
            "context": foregroundContext()
        ]
        for (key, value) in fields {
            payload[key] = value
        }
        writeJSON(payload)
    }
}

func emitKeyInput(event: CGEvent) {
    let observedAt = epochMs()
    let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
    let text = characters(from: event)
    captureQueue.async {
        let context = foregroundContext()
        if !isSupportedTerminalContext(context) {
            return
        }

        var fields: [String: Any] = [
            "key_code": keyCode
        ]
        if let text, !text.isEmpty {
            fields["text"] = text
        }

        var payload: [String: Any] = [
            "type": "event",
            "event_name": "key_input",
            "observed_at": observedAt,
            "context": context
        ]
        for (key, value) in fields {
            payload[key] = value
        }
        writeJSON(payload)
    }
}

let eventCallback: CGEventTapCallBack = { _, type, event, _ in
    switch type {
    case .leftMouseDown:
        emitEvent(name: "mouse_click", fields: ["button": "left"])
    case .rightMouseDown:
        emitEvent(name: "mouse_click", fields: ["button": "right"])
    case .otherMouseDown:
        emitEvent(name: "mouse_click", fields: ["button": "other"])
    case .keyDown:
        emitKeyInput(event: event)
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        if keyCode == 36 || keyCode == 76 {
            emitEvent(name: "enter_key", fields: ["key_code": keyCode])
        }
    case .tapDisabledByTimeout, .tapDisabledByUserInput:
        emitStatus("degraded", error: "event tap disabled")
    default:
        break
    }
    return Unmanaged.passUnretained(event)
}

let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: promptPermissions] as CFDictionary
let trusted = AXIsProcessTrustedWithOptions(options)
emitStatus(trusted ? "starting" : "degraded", error: trusted ? nil : "macOS Accessibility permission is not granted")

let mask =
    (1 << CGEventType.leftMouseDown.rawValue) |
    (1 << CGEventType.rightMouseDown.rawValue) |
    (1 << CGEventType.otherMouseDown.rawValue) |
    (1 << CGEventType.keyDown.rawValue)

guard let eventTap = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .listenOnly,
    eventsOfInterest: CGEventMask(mask),
    callback: eventCallback,
    userInfo: nil
) else {
    emitStatus("failed", error: "failed to create CGEvent tap; grant Accessibility/Input Monitoring permission")
    Thread.sleep(forTimeInterval: 0.2)
    exit(2)
}

let runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, eventTap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
CGEvent.tapEnable(tap: eventTap, enable: true)
emitStatus("running")
CFRunLoopRun()
