pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Remote-host hardware meters, styled to match the MACHINE card Meter bars.
// The LOCAL host is intentionally NOT shown here: its CPU/RAM/disk already live
// in the cockpit above, and its discrete GPUs are rendered directly in the
// cockpit grid (InfoView.qml). This panel only adds remote hosts (e.g.
// spark-station), each with its own CPU/RAM/disk/GPU meters.
// No per-host "updated at" text - freshness is a status dot on the host header.
Flickable {
  id: root
  required property var monitor
  required property var desk
  required property var style
  property bool interactivePanel: true
  implicitHeight: Math.min(contentHeight, Math.round(320 * root.style.fontScale))
  contentHeight: rows.implicitHeight
  contentWidth: width
  clip: true
  interactive: interactivePanel
  boundsBehavior: Flickable.StopAtBounds
  ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

  function bytes(value) { return value === null || value === undefined ? "—" : desk.bytes(value) }
  function pct(value) { return value === null || value === undefined ? "—" : Math.round(Number(value)) + "%" }

  // A single meter: label + value on top, colored fill bar below. Mirrors the
  // MACHINE card Meter component so the two read as one system.
  component Bar: Item {
    required property string label
    required property string value
    required property real fraction
    required property color tone
    implicitHeight: mrow.implicitHeight + track.height + root.style.spacing.xs
    width: parent ? parent.width : 200
    RowLayout {
      id: mrow
      width: parent.width
      Text { text: label; color: root.desk.themeForeground; opacity: 0.62; textFormat: Text.PlainText; font.family: root.style.resolvedFontFamily; font.pixelSize: root.style.font.bodySmall; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: Math.round(parent.width * 0.55) }
      Item { Layout.fillWidth: true; Layout.minimumWidth: root.style.spacing.sm }
      Text { text: value; color: root.desk.themeForeground; textFormat: Text.PlainText; font.family: root.style.resolvedFontFamily; font.pixelSize: root.style.font.bodySmall; elide: Text.ElideRight; horizontalAlignment: Text.AlignRight; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: Math.round(parent.width * 0.7) }
    }
    Rectangle {
      id: track
      anchors { top: mrow.bottom; topMargin: root.style.spacing.xs; left: parent.left; right: parent.right }
      height: Math.max(3, Math.round(4 * root.style.fontScale))
      radius: height / 2
      color: Qt.rgba(1, 1, 1, 0.10)
      Rectangle {
        width: parent.width * Math.max(0, Math.min(1, fraction))
        height: parent.height; radius: parent.radius; color: tone
        Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
      }
    }
  }

  Column {
    id: rows
    width: root.width - 12
    spacing: root.style.spacing.md

    // Remote hosts only: the local host (id "local") is rendered in the cockpit.
    Repeater {
      model: (root.monitor.hosts || []).filter(function (h) { return h.id !== "local" })
      delegate: Column {
        id: hostBlock
        required property var modelData
        readonly property var stats: modelData.stats || ({})
        readonly property var cpu: hostBlock.stats.cpu || ({})
        readonly property var mem: hostBlock.stats.mem || ({})
        readonly property var disks: hostBlock.stats.disks || []
        readonly property var gpus: hostBlock.stats.gpus || []
        readonly property bool offline: modelData.status === "offline"
        readonly property bool stale: modelData.stale || root.monitor.stale
        width: rows.width
        spacing: root.style.spacing.xs

        // Host header: name + status dot. No timestamp.
        RowLayout {
          width: parent.width
          spacing: root.style.spacing.xs
          Rectangle {
            width: 8; height: 8; radius: 4
            color: hostBlock.offline ? root.desk.red : (hostBlock.stale ? root.desk.yellow : root.desk.green)
          }
          Text {
            text: hostBlock.modelData.label + (hostBlock.offline ? " · offline" : (hostBlock.stale ? " · stale" : ""))
            color: hostBlock.offline ? root.desk.red : (hostBlock.stale ? root.desk.yellow : root.desk.themeForeground)
            textFormat: Text.PlainText; font.family: root.style.resolvedFontFamily; font.pixelSize: root.style.font.caption; font.bold: true
            Layout.fillWidth: true; Layout.minimumWidth: 0; elide: Text.ElideRight
          }
        }

        // A host with no stats yet (collecting) or offline shows one quiet line.
        Text {
          visible: !hostBlock.modelData.stats
          text: hostBlock.offline ? (hostBlock.modelData.error || "unreachable") : "collecting…"
          color: hostBlock.offline ? root.desk.red : root.desk.themeForeground
          opacity: 0.5; textFormat: Text.PlainText; font.family: root.style.resolvedFontFamily; font.pixelSize: root.style.font.caption
        }

        // CPU
        Bar {
          visible: !!hostBlock.modelData.stats
          label: "CPU"
          value: root.pct(hostBlock.cpu.pct)
          fraction: (Number(hostBlock.cpu.pct) || 0) / 100
          tone: (Number(hostBlock.cpu.pct) || 0) > 85 ? root.desk.red : root.desk.blue
        }
        // RAM
        Bar {
          visible: !!hostBlock.modelData.stats
          label: "RAM"
          value: root.bytes(hostBlock.mem.used) + "/" + root.bytes(hostBlock.mem.total)
          fraction: Number(hostBlock.mem.total) > 0 ? Number(hostBlock.mem.used) / Number(hostBlock.mem.total) : 0
          tone: (Number(hostBlock.mem.total) > 0 && Number(hostBlock.mem.used) / Number(hostBlock.mem.total) > 0.9) ? root.desk.red : root.desk.green
        }
        // Disks (top 2, matching the cockpit)
        Repeater {
          model: hostBlock.disks.slice(0, 2)
          delegate: Bar {
            required property var modelData
            visible: !!hostBlock.modelData.stats
            label: "DISK " + modelData.mount
            value: root.bytes(modelData.used) + "/" + root.bytes(modelData.total)
            fraction: Number(modelData.total) > 0 ? Number(modelData.used) / Number(modelData.total) : 0
            tone: (Number(modelData.total) > 0 && Number(modelData.used) / Number(modelData.total) > 0.9) ? root.desk.red : root.desk.yellow
          }
        }
        // GPUs - one bar each; bar = VRAM headroom. Unified-memory GPUs (no
        // discrete VRAM) drop the VRAM segment and fall back to util for the bar.
        Repeater {
          model: hostBlock.gpus
          delegate: Bar {
            required property var modelData
            visible: !!hostBlock.modelData.stats
            label: "GPU " + modelData.id + " " + modelData.name
            value: root.pct(modelData.util) + (modelData.memTotal ? " · " + root.bytes(modelData.memUsed) + "/" + root.bytes(modelData.memTotal) : "") + " · " + (modelData.temp === null || modelData.temp === undefined ? "—" : Math.round(modelData.temp) + "°")
            fraction: modelData.memTotal ? Number(modelData.memUsed) / Number(modelData.memTotal) : ((Number(modelData.util) || 0) / 100)
            tone: root.desk.green
          }
        }
        // No GPUs detected on this host
        Text {
          visible: !!hostBlock.modelData.stats && hostBlock.gpus.length === 0
          text: "GPU · none detected"
          color: root.desk.themeForeground; opacity: 0.5; textFormat: Text.PlainText; font.family: root.style.resolvedFontFamily; font.pixelSize: root.style.font.caption
        }
      }
    }
  }
}
