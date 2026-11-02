const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/sdk.js', 'utf8');
const id = 'a'.repeat(32);
const tick = () => new Promise(resolve => setImmediate(resolve));
async function lab(failureType) {
  const nodes = new Map(), handlers = {}, classes = new Set(), calls = [], navigations = [];
  let config, stopped = 0;
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {hidden: false, disabled: false, textContent: '', showModal() { this.open = true; }, addEventListener(name, callback) { this[name] = callback; }});
    return nodes.get(selector);
  };
  const document = {querySelector: node, querySelectorAll: () => [{srcObject: {getTracks: () => [{stop() { stopped++; }}]}}], body: {classList: {add: value => classes.add(value)}, append() {}}, fullscreenElement: null};
  const originalFetch = async (path, options) => {
    calls.push(String(path));
    if (String(path).endsWith('/config')) return Response.json({HEADERS_REQUEST: {'X-CSRF-Token': 'local-csrf'}, SDK_FLOW: 'FACE'});
    if (String(path).includes('/proxy/')) {
      if (failureType === 'network') throw new TypeError('Network failed');
      if (failureType === 'html') return new Response('<html>bad gateway</html>', {status: 502});
      if (failureType === 'api') return Response.json({message: 'LAB_VNPT_TIMEOUT', error: 'VNPT phản hồi quá thời gian chờ.', error_kind: 'timeout'}, {status: 504});
      return Response.json({message: 'IDG-00000000', object: {liveness: 'success'}});
    }
    if (String(path).endsWith('/finish')) return Response.json({status: failureType ? 'error' : 'completed', calls: 1, requests: []});
    return Response.json({status: 'running', calls: 0, requests: [], error: null});
  };
  const window = {fetch: originalFetch, SDK: {launch: value => { config = value; }}, FaceVNPTBrowserSDK: {}, addEventListener: (name, callback) => { handlers[name] = callback; }};
  const context = {window, document, location: {hash: `#run=${id}`, origin: 'https://testserver', replace: url => navigations.push(url)}, URL, URLSearchParams, Response, AbortController, setTimeout: (...args) => {const timer = setTimeout(...args); timer.unref(); return timer;}, clearTimeout};
  vm.runInNewContext(source, context);
  for (let i = 0; i < 20 && !config; i++) await tick();
  assert.equal(config.SDK_FLOW, 'FACE');
  return {config, nodes, node, classes, calls, window, handlers, navigations, stopped: () => stopped};
}
for (const kind of ['api', 'html', 'network']) test(`SDK exits loading and blocks further uploads after ${kind} error`, async () => {
  const app = await lab(kind);
  const url = `https://testserver/api/sdk/runs/${id}/proxy/file-service/v1/addFile`;
  try { await app.window.fetch(url, {method: 'POST'}); } catch (error) { assert.equal(kind, 'network'); }
  for (let i = 0; i < 8; i++) await tick();
  assert(app.classes.has('sdk-interrupted'));
  assert.equal(app.node('#sdk-failure').hidden, false);
  assert.equal(app.node('#sdk-failure').open, true, 'error is in the browser top layer above SDK modals');
  assert(app.node('#sdk-failure-message').textContent.length > 10);
  assert(app.stopped() > 0);
  assert.equal(app.calls.filter(call => call.endsWith('/finish')).length, 1);
  assert.equal(app.calls.filter(call => call.includes('/proxy/')).length, 1);
  const blocked = await app.window.fetch(url, {method: 'POST'});
  assert.equal(blocked.status, 409);
  assert.equal(app.calls.filter(call => call.includes('/proxy/')).length, 1);
  assert.equal(app.navigations.length, 0, 'keep the error visible until the user chooses results');
  await app.node('#sdk-view-results').click();
  assert.equal(app.navigations[0], '/?tab=oval');
});
test('an unrelated extension rejection does not interrupt the SDK', async () => {
  const app = await lab();
  app.handlers.unhandledrejection({reason: {stack: 'TypeError at chrome-extension://unrelated/executors/test.js'}});
  assert.equal(app.classes.has('sdk-interrupted'), false);
  assert.equal(app.calls.filter(call => call.endsWith('/finish')).length, 0);
});
test('successful SDK callback returns to the independent Oval results', async () => {
  const app = await lab();
  await app.window.fetch(`https://testserver/api/sdk/runs/${id}/proxy/ai/v1/web/face/liveness-3d`, {method: 'POST'});
  app.config.CALL_BACK_END_FLOW();
  for (let i = 0; i < 20 && !app.navigations.length; i++) await tick();
  assert.equal(app.navigations[0], '/?tab=oval');
  assert.equal(app.classes.has('sdk-interrupted'), false);
});
