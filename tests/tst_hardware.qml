import QtQuick
import QtTest
import "../" as Plugin

TestCase {
  name: "HardwarePanel"
  when: windowShown
  width: 480
  height: 500
  QtObject {
    id: fixtureDesk
    property color themeForeground: "white"
    property color red: "red"
    property color yellow: "yellow"
    property color green: "green"
    property color blue: "blue"
    function bytes(n) { return n === null || n === undefined ? "—" : String(n) + " B" }
  }
  QtObject {
    id: fixtureMonitor
    property string error: ""
    property bool stale: false
    property double clock: 100000
    property var hosts: [
      // Local host: must be FILTERED OUT (rendered in the cockpit instead).
      {id: "local", label: "Local", status: "online", stale: false, error: "",
        stats: {cpu: {pct: 12}, mem: {used: 100, total: 200}, disks: [{mount: "/", used: 10, total: 20}],
          gpus: [{id: "0000:04:00.0", name: "Intel Arc Pro B70", driver: "xe", util: 32, memUsed: 2000000000, memTotal: 34000000000, temp: 57}]}},
      // Remote host: must be RENDERED with its own meters.
      {id: "spark", label: "Spark", status: "online", stale: false, error: "",
        stats: {cpu: {pct: 6}, mem: {used: 96500, total: 122000}, disks: [{mount: "/", used: 642000, total: 3700000}],
          gpus: [{id: "0000:f:01:00.0", name: "NVIDIA GB10", driver: "nvidia", util: 0, memUsed: null, memTotal: null, temp: 42}]}},
      // Offline remote host: labelled with its error.
      {id: "other", label: "Other", status: "offline", stale: true, error: "Test offline",
        stats: null}
    ]
  }
  Plugin.HardwarePanel {
    id: panel
    width: 440
    height: implicitHeight
    desk: fixtureDesk
    monitor: fixtureMonitor
    style: ({fontScale: 1, resolvedFontFamily: "monospace", font: {caption: 12, bodySmall: 11}, spacing: {xs: 3, sm: 6, md: 6}})
  }
  function texts(item) {
    var result = item.text !== undefined ? [String(item.text)] : []
    for (var i = 0; i < item.children.length; i++) result = result.concat(texts(item.children[i]))
    return result
  }
  function test_remote_only_and_nulls() {
    wait(50)
    var rendered = texts(panel).join("\n")
    // Local host is filtered out (its GPU id must NOT appear).
    verify(rendered.indexOf("Local") < 0)
    verify(rendered.indexOf("0000:04:00.0") < 0)
    // Remote host is rendered with its meters.
    verify(rendered.indexOf("Spark") >= 0)
    verify(rendered.indexOf("CPU") >= 0)
    verify(rendered.indexOf("RAM") >= 0)
    verify(rendered.indexOf("DISK /") >= 0)
    verify(rendered.indexOf("0000:f:01:00.0") >= 0)
    // Unified-memory GPU: no VRAM segment, shows util + temp.
    verify(rendered.indexOf("42°") >= 0)
    // Offline remote host is labelled with its error.
    verify(rendered.indexOf("Other") >= 0)
    verify(rendered.indexOf("offline") >= 0)
    verify(rendered.indexOf("Test offline") >= 0)
    // The verbose "last good / updated at" chunk is gone.
    verify(rendered.indexOf("last good") < 0)
    verify(rendered.indexOf("updated") < 0)
    verify(panel.implicitHeight > 0)
    verify(panel.contentHeight >= panel.height)
    // Null-safe helpers.
    compare(panel.bytes(null), "—")
    compare(panel.pct(null), "—")
    compare(panel.pct(0), "0%")
  }
}
