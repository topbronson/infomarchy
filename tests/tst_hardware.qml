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
    property color cyan: "cyan"
    function bytes(n) { return n === null || n === undefined ? "—" : String(n) + " B" }
    function rate(n) { return n === null || n === undefined ? "—" : String(Math.round(n)) + "B/s" }
    function dur(sec) { sec = Math.max(0, Math.floor(Number(sec || 0))); var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60); if (d > 0) return d + "d " + h + "h"; if (h > 0) return h + "h " + m + "m"; return m + "m" }
  }
  QtObject {
    id: fixtureMonitor
    property string error: ""
    property bool stale: false
    property double clock: 100000
    property var hosts: [
      // Local host: must be FILTERED OUT (rendered in the cockpit instead).
      {id: "local", label: "Local", user: "top-bronson", status: "online", stale: false, error: "", netRate: null,
        stats: {cpu: {pct: 12}, mem: {used: 100, total: 200}, disks: [{mount: "/", used: 10, total: 20}],
          gpus: [{id: "0000:04:00.0", name: "Intel Arc Pro B70", driver: "xe", util: 32, memUsed: 2000000000, memTotal: 34000000000, temp: 57}],
          uptime: 3600, net: {dev: "eth0", addr: "10.0.0.5", wan: "1.2.3.4", rx: 100, tx: 200, wireless: false, ssid: null, signal: null},
          ping: {ok: true, ms: 10}, hostname: "omarchy-station"}},
      // Remote host: must be RENDERED with its own meters + footer + user@hostname header.
      {id: "spark", label: "Spark", user: "top-bronson", status: "online", stale: false, error: "", netRate: [1024, 512],
        stats: {cpu: {pct: 6}, mem: {used: 96500, total: 122000}, disks: [{mount: "/", used: 642000, total: 3700000}],
          gpus: [{id: "0000:f:01:00.0", name: "NVIDIA GB10", driver: "nvidia", util: 0, memUsed: null, memTotal: null, temp: 42}],
          uptime: 90000, net: {dev: "eth0", addr: "100.110.189.112", wan: "9.9.9.9", rx: 5000, tx: 6000, wireless: false, ssid: null, signal: null},
          ping: {ok: true, ms: 12}, hostname: "spark-station"}},
      // Offline remote host: labelled with its error.
      {id: "other", label: "Other", user: "top-bronson", status: "offline", stale: true, error: "Test offline", netRate: null,
        stats: null}
    ]
  }
  Plugin.HardwarePanel {
    id: panel
    width: 440
    height: implicitHeight
    desk: fixtureDesk
    monitor: fixtureMonitor
    style: ({fontScale: 1, resolvedFontFamily: "monospace", font: {caption: 12, bodySmall: 11}, spacing: {xs: 3, sm: 6, md: 6, lg: 8}})
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
    verify(rendered.indexOf("0000:04:00.0") < 0)
    verify(rendered.indexOf("omarchy-station") < 0)
    // Remote host header is "user@hostname · up <dur>" like the cockpit.
    verify(rendered.indexOf("top-bronson@spark-station") >= 0)
    verify(rendered.indexOf("up 1d 1h") >= 0)
    // Remote host is rendered with its meters.
    verify(rendered.indexOf("CPU") >= 0)
    verify(rendered.indexOf("RAM") >= 0)
    verify(rendered.indexOf("DISK /") >= 0)
    verify(rendered.indexOf("0000:f:01:00.0") >= 0)
    // Unified-memory GPU: no VRAM segment, shows util + temp.
    verify(rendered.indexOf("42°") >= 0)
    // Footer: WAN/LAN + rates/ping for the remote host.
    verify(rendered.indexOf("WAN 9.9.9.9") >= 0)
    verify(rendered.indexOf("LAN 100.110.189.112") >= 0)
    verify(rendered.indexOf("⇄ 12 ms") >= 0)
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
