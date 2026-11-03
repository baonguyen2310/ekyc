const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync("app/static/nfc.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
function lab() {
  const nodes = new Map(), calls = [], replies = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {hidden: false, disabled: false, checked: false, textContent: "", classList: {toggle() {}}, addEventListener(event, handler) { this[event] = handler; }, setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; }, replaceChildren() {}, append() {}});
    return nodes.get(id);
  }
  const context = {window: {}, document: {getElementById: node, createElement: () => node(Symbol())}, api: async (path, options) => { calls.push({path, options}); return replies.shift(); }, jsonOptions: (method, body) => ({method, body}), setTimeout, URL, Blob};
  vm.runInNewContext(source, context);
  return {nfc: context.window.nfcLab, node: id => node("nfc-" + id), calls, replies};
}
test("NFC tab makes no outbound request when opened and disables start without config", async () => {
  const app = lab();
  assert.equal(app.calls.length, 0);
  app.replies.push({ready: false, message: "Chưa cấu hình", latest_run: null});
  await app.nfc.load(); app.nfc.select("nfc");
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].path, "/api/nfc/status");
  assert.equal(app.node("start").disabled, true);
  assert.equal(app.node("connection").textContent, "Chưa cấu hình");
});
test("NFC creation requires consent and refresh happens only on button click", async () => {
  const app = lab();
  app.replies.push({ready: true, message: "Sandbox", latest_run: null});
  await app.nfc.load(); app.nfc.select("nfc");
  app.node("start").click();
  assert.equal(app.calls.length, 1);
  assert.match(app.node("error").textContent, /đồng ý/);
  app.node("consent").checked = true;
  const run = {id: "synthetic-run", status: "pending", capture_url: "https://lab.sandbox.signicat.com/capture/#artifact=test", calls: 3, log: []};
  app.replies.push(run); app.node("start").click(); await tick();
  assert.equal(app.calls.length, 2);
  assert.equal(app.node("open").href, run.capture_url);
  assert.equal(app.node("start").hidden, true);
  app.replies.push({...run, status: "accepted", capture_url: null, checks: [], fields: [], raw: {status: "accepted"}});
  app.node("refresh").click(); await tick();
  assert.equal(app.calls.length, 3);
  assert.equal(app.calls[2].path, "/api/nfc/runs/synthetic-run/refresh");
  assert.equal(app.node("refresh").hidden, true);
  assert.equal(app.node("open").href, undefined);
  app.nfc.clear();
  assert.equal(app.node("result").hidden, true);
  assert.equal(app.node("json").textContent, "");
});
test("NFC logout prevents an in-flight response from repainting personal data", async () => {
  const app = lab();
  let complete;
  app.replies.push(new Promise(resolve => {complete = resolve;}));
  const loading = app.nfc.load();
  app.nfc.clear();
  complete({ready: true, latest_run: {id: "old", status: "accepted", fields: [{label: "Tên", value: "old personal data"}]}});
  await loading;
  assert.equal(app.node("result").hidden, true);
  assert.equal(app.node("start").disabled, true);
});
