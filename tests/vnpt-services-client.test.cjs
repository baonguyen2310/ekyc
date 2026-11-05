const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync("app/static/vnpt-services.js", "utf8");

function lab(reply) {
  const nodes = new Map(), calls = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, textContent: "", children: [],
      classList: {toggle() {}},
      setAttribute(key, value) { this[key] = value; },
      replaceChildren() { this.children = []; },
      append(...children) { this.children.push(...children); }
    });
    return nodes.get(id);
  }
  const context = {
    window: {},
    document: {getElementById: node, createElement: () => node(Symbol())},
    api: async path => { calls.push(path); return await reply; }
  };
  vm.runInNewContext(source, context);
  return {services: context.window.vnptServices, node, calls};
}

test("switching VNPT tabs sends no requests and shows one panel at a time", async () => {
  const app = lab({idcheck: {message: "Chưa kết nối"}, vneid: {message: "Chưa kết nối"}});
  app.services.select("idcheck");
  assert.equal(app.node("idcheck-panel").hidden, false);
  assert.equal(app.node("vneid-panel").hidden, true);
  app.services.select("vneid");
  assert.equal(app.node("idcheck-panel").hidden, true);
  assert.equal(app.node("vneid-panel").hidden, false);
  app.services.select("nfc");
  assert.equal(app.node("vneid-panel").hidden, true);
  assert.equal(app.calls.length, 0);
  await app.services.load();
  assert.deepEqual(app.calls, ["/api/vnpt-services/status"]);
});

test("logout discards a delayed availability response", async () => {
  let finish;
  const app = lab(new Promise(resolve => { finish = resolve; }));
  const pending = app.services.load();
  app.services.clear();
  finish({idcheck: {message: "old session"}, vneid: {message: "old session"}});
  await pending;
  assert.notEqual(app.node("idcheck-connection").textContent, "old session");
});
