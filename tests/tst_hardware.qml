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
    function bytes(n) { return String(n) + " B" }
  }
  QtObject {
    id: fixtureMonitor
    property string error: ""
    property bool stale: false
    property double clock: 100000
    property var hosts: [{id: "test", label: "Test host", status: "offline", stale: true, error: "Test offline", lastSuccess: 10000, lastAttempt: 90000,
      stats: {cpu: {pct: 12}, mem: {used: 100, total: 200}, disks: [{mount: "/", used: 10, total: 20}],
        gpus: [{id: "0000:04:00.0", name: "Intel", driver: "xe", util: null, memUsed: null, memTotal: null, temp: 58},
               {id: "0000:09:00.0", name: "Intel", driver: "xe", util: null, memUsed: null, memTotal: null, temp: 44}]}}]
  }
  Plugin.HardwarePanel { id: panel; width: 440; height: implicitHeight; desk: fixtureDesk; monitor: fixtureMonitor; style: ({fontScale: 1, font: {caption: 12}, spacing: {sm: 6, xs: 3}}) }
  function texts(item) {
    var result = item.text !== undefined ? [String(item.text)] : []
    for (var i = 0; i < item.children.length; i++) result = result.concat(texts(item.children[i]))
    return result
  }
  function test_rows_and_nulls() {
    wait(50)
    var rendered = texts(panel).join("\n")
    verify(rendered.indexOf("0000:04:00.0") >= 0)
    verify(rendered.indexOf("0000:09:00.0") >= 0)
    verify(rendered.indexOf("VRAM —/—") >= 0)
    verify(rendered.indexOf("last good (stale)") >= 0)
    verify(rendered.indexOf("Test offline") >= 0)
    verify(panel.implicitHeight > 0)
    verify(panel.contentHeight >= panel.height)
    compare(panel.metric(null, "%"), "—")
    compare(panel.metric(0, "%"), "0%")
  }
}
