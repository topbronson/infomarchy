pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Flickable {
  id: root
  required property var monitor
  required property var desk
  required property var style
  property bool interactivePanel: true
  implicitHeight: Math.min(contentHeight, Math.round(230 * root.style.fontScale))
  contentHeight: rows.implicitHeight
  contentWidth: width
  clip: true
  interactive: interactivePanel
  boundsBehavior: Flickable.StopAtBounds
  ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

  function metric(value, unit) { return value === null || value === undefined ? "—" : Number(value).toFixed(0) + unit }
  function bytes(value) { return value === null || value === undefined ? "—" : desk.bytes(value) }
  function stamp(value) { return value ? new Date(value).toLocaleString() : "never" }

  component Line: Text {
    width: parent.width
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    font.family: "monospace"
    font.pixelSize: root.style.font.caption
    color: root.desk.themeForeground
  }
  Column {
    id: rows
    width: root.width - 12
    spacing: root.style.spacing.sm
    Line { text: "HARDWARE HOSTS · scroll for all devices"; font.bold: true }
    Line { visible: !!root.monitor.error; text: root.monitor.error; color: root.desk.red }
    Line { visible: root.monitor.hosts.length === 0; text: "Collecting hardware · python3 required" }
    Repeater {
      model: root.monitor.hosts
      delegate: Column {
        id: hostRow
        required property var modelData
        readonly property var stats: modelData.stats || ({})
        readonly property bool stale: modelData.stale || root.monitor.stale || !modelData.lastSuccess || root.monitor.clock - modelData.lastSuccess > 30000
        width: rows.width
        spacing: root.style.spacing.xs
        Line { text: hostRow.modelData.label + " · " + (root.monitor.stale ? "stale" : hostRow.modelData.status) + (hostRow.stale && hostRow.modelData.stats ? " · last good (stale)" : ""); color: hostRow.stale ? root.desk.yellow : root.desk.green; font.bold: true }
        Line { text: "Updated " + root.stamp(hostRow.modelData.lastSuccess) + " · attempted " + root.stamp(hostRow.modelData.lastAttempt); opacity: 0.65 }
        Line { visible: !!hostRow.modelData.error; text: hostRow.modelData.error; color: root.desk.red }
        Line { visible: !!hostRow.modelData.stats; text: "CPU " + root.metric((hostRow.stats.cpu || {}).pct, "%") + " · RAM " + root.bytes((hostRow.stats.mem || {}).used) + "/" + root.bytes((hostRow.stats.mem || {}).total) }
        Repeater {
          model: hostRow.stats.disks || []
          delegate: Line { required property var modelData; text: "Disk " + modelData.mount + " · " + root.bytes(modelData.used) + "/" + root.bytes(modelData.total) }
        }
        Repeater {
          model: hostRow.stats.gpus || []
          delegate: Line {
            required property var modelData
            text: "GPU " + modelData.id + " · " + modelData.name + (modelData.driver ? " (" + modelData.driver + ")" : "") + "\n  " + root.metric(modelData.util, "%") + " · VRAM " + root.bytes(modelData.memUsed) + "/" + root.bytes(modelData.memTotal) + " · " + root.metric(modelData.temp, "°C")
          }
        }
        Line { visible: !!hostRow.modelData.stats && !(hostRow.stats.gpus || []).length; text: "GPU · none detected / unavailable"; opacity: 0.65 }
      }
    }
  }
}
