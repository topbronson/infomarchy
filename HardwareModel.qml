import QtQuick
import Quickshell.Io

Item {
  id: root
  property bool active: true
  property var hosts: []
  property string error: ""
  property double clock: Date.now()
  property double receivedAt: 0
  readonly property bool stale: clock - receivedAt > 30000
  readonly property string script: decodeURIComponent(Qt.resolvedUrl("hardware-hosts.py").toString().replace(/^file:\/\//, ""))

  onActiveChanged: if (!active) worker.running = false
  Process {
    id: worker
    command: ["python3", root.script]
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) {
        if (line.length > 262144) { root.error = "Hardware frame too large"; worker.running = false; return }
        try {
          var frame = JSON.parse(line)
          if (!Array.isArray(frame.hosts) || frame.hosts.length > 5) throw new Error("Invalid hardware hosts")
          root.hosts = frame.hosts
          root.error = String(frame.error || "").slice(0, 256)
          root.receivedAt = Date.now()
        } catch (e) { root.error = "Invalid hardware frame" }
      }
    }
    stderr: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.error = String(line).slice(0, 256) }
    }
    onExited: function(code) { if (root.active) root.error = "Hardware worker stopped (" + code + "); python3 is required" }
  }
  Timer {
    interval: 1000
    running: root.active
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.clock = Date.now()
      if (!worker.running) worker.running = true
    }
  }
}
