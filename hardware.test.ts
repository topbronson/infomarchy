import { expect, test } from "bun:test";
import { readFileSync } from "fs";
import { join } from "path";

const read = (name: string) => {
  try { return readFileSync(join(import.meta.dir, name), "utf8"); } catch { return ""; }
};

test("hardware monitor is separate from the AI collector and pauses with its surface", () => {
  expect(read("InfoModel.qml")).toContain("HardwareModel {");
  const source = read("HardwareModel.qml");
  expect(source).toContain('Qt.resolvedUrl("hardware-hosts.py")');
  expect(source).toContain("SplitParser");
  expect(source).toContain("root.active");
  expect(source).not.toContain("collector.ts");
});

test("machine card has bounded scrolling per-host rows, GPU identity and freshness", () => {
  expect(read("InfoView.qml")).toContain("HardwarePanel {");
  // Card.body only measures a sole child: sibling content would overlap and
  // collapse the entire machine card's implicit height.
  expect(read("InfoView.qml")).toMatch(/id: mc\s+Column\s*\{/);
  const source = read("HardwarePanel.qml");
  for (const marker of ["Flickable", "component Bar", "stale", "gpus", "modelData.id", "GPU ", "CPU", "RAM", 'textFormat: Text.PlainText'])
    expect(source).toContain(marker);
});

test("hardware panel renders multiple devices and last-good offline rows", () => {
  const result = Bun.spawnSync(["/usr/lib/qt6/bin/qmltestrunner", "-input", join(import.meta.dir, "tests/tst_hardware.qml")], {
    env: { ...process.env, QT_QPA_PLATFORM: "offscreen", QT_QUICK_BACKEND: "software" }, timeout: 15000,
  });
  expect(result.exitCode, result.stdout.toString() + result.stderr.toString()).toBe(0);
}, 20000);

test("hardware standard-library regression suite", () => {
  const result = Bun.spawnSync(["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_hardware*.py", "-v"], { cwd: import.meta.dir });
  expect(result.exitCode, result.stdout.toString() + result.stderr.toString()).toBe(0);
});
