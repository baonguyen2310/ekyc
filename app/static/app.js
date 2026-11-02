"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {csrf: "", providerReady: false, providerMessage: "", busy: false, images: {}, urls: {}, stream: null, slot: null, result: null, timer: null};
const mode = () => document.querySelector('input[name="mode"]:checked').value;
const labels = {front: "mặt trước", back: "mặt sau", selfie: "chân dung"};
Object.assign(state, {flow: "images", sdkRun: null, sdkRunFlow: null, sdkStarting: false, results: {images: null, sdk: null, full: null}});
const resultPanels = {images: $("#result-panel")};
const sdkFlows = {
  sdk: {requestFlow: "oval", tab: "oval", prefix: "oval-", title: "Kết quả Liveness Oval", note: "Oval kiểm tra người thật và che mặt. Kết quả này độc lập với OCR, chưa xác nhận người đó là chủ giấy tờ."},
  full: {requestFlow: "document_face", tab: "full", prefix: "full-", title: "Kết quả luồng đầy đủ", note: "So khớp đối chiếu ảnh trên CCCD với ảnh khuôn mặt ghi nhận trong lượt quét Oval này. Chỉ các bước có phản hồi từ VNPT mới được đánh giá; điểm so khớp không phải xác suất đúng danh tính. Không xác thực chip hoặc đối chiếu C06/VNeID."}
};
for (const [flow, config] of Object.entries(sdkFlows)) {
  const panel = resultPanels.images.cloneNode(true);
  panel.id = `${config.prefix}result-panel`;
  panel.querySelectorAll("[id]").forEach(node => { node.id = config.prefix + node.id; });
  panel.querySelector("h2").textContent = config.title;
  panel.querySelector(".result-note").textContent = config.note;
  $(`#${flow}-panel`).insertAdjacentElement("afterend", panel);
  resultPanels[flow] = panel;
}
function sdkControls(flow) {
  return {start: $(`#start-${flow}`), stop: $(`#stop-${flow}`), consent: $(`#${flow}-consent`), error: $(`#${flow}-error`), status: $(`#${flow}-status`)};
}
const resultFlow = result => result.flow === "document_face" || result.mode === "sdk" ? "full" : result.mode === "oval" ? "sdk" : "images";

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.csrf) headers.set("X-CSRF-Token", state.csrf);
  const response = await fetch(path, {credentials: "same-origin", ...options, headers});
  let data;
  try { data = await response.json(); } catch { throw new Error("Service chưa phản hồi đúng. Vui lòng thử lại sau."); }
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") showAccess();
    throw new Error(typeof data.detail === "string" ? data.detail : "Yêu cầu chưa hợp lệ. Vui lòng kiểm tra lại.");
  }
  return data;
}
function jsonOptions(method, data) { return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)}; }
function toast(message) { $("#toast").textContent = message; $("#toast").hidden = false; clearTimeout(state.timer); state.timer = setTimeout(() => $("#toast").hidden = true, 3500); }
function showAccess() {
  state.sdkRun = null; state.sdkRunFlow = null; state.sdkStarting = false;
  $("#access-panel").hidden = false; $("#workspace").hidden = true; $("#header-actions").hidden = true;
  state.csrf = ""; state.providerReady = false; state.result = null; clearTimeout(state.poll); setBusy(false);
  state.results = {images: null, sdk: null, full: null};
  for (const slot of Object.keys(state.images)) clearImage(slot);
  $("#consent").checked = false;
  $("#camera-dialog").close(); stopCamera();
  for (const panel of Object.values(resultPanels)) {
    panel.hidden = true;
    panel.querySelectorAll("pre, dl, .check-results, tbody").forEach(node => node.replaceChildren());
  }
  for (const flow of Object.keys(sdkFlows)) {
    const controls = sdkControls(flow);
    controls.consent.checked = false; controls.error.textContent = ""; controls.status.textContent = "";
  }
}
function syncConnection() {
  $("#service-notice").hidden = state.providerReady;
  $("#service-message").textContent = state.providerMessage;
  $("#connection-dot").classList.toggle("connected", state.providerReady);
  setBusy(state.busy);
}
async function openWorkspace() {
  const data = await api("/api/status");
  state.sdkRun = null; state.sdkRunFlow = null; state.sdkStarting = false;
  state.csrf = data.csrf; state.providerReady = data.provider_ready; state.providerMessage = data.provider_message;
  $("#access-panel").hidden = true; $("#workspace").hidden = false; $("#header-actions").hidden = false;
  syncConnection();
  if (data.latest_job) await pollJob(data.latest_job);
  const previousRuns = data.latest_sdk_by_flow ? Object.values(data.latest_sdk_by_flow) : data.latest_sdk ? [data.latest_sdk] : [];
  for (const id of previousRuns) {
    const previous = await api(`/api/sdk/runs/${id}`);
    renderResult(previous);
    if (previous.status === "running") {
      state.sdkRun = previous.id;
      state.sdkRunFlow = resultFlow(previous);
      sdkControls(state.sdkRunFlow).status.textContent = "Lượt camera trước vẫn đang mở. Kết thúc lượt này để xem kết quả và bắt đầu lại.";
    }
    syncSdkControls();
  }
  const tab = new URLSearchParams(location.search).get("tab");
  selectFlow(tab === "full" ? "full" : tab === "oval" ? "sdk" : "images");
}
window.addEventListener("pageshow", event => {
  if (event.persisted) openWorkspace().catch(showAccess);
});
$("#access-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("#access-error").textContent = "";
  const button = event.submitter; button.disabled = true;
  try { await api("/api/login", jsonOptions("POST", {access_code: $("#access-code").value})); $("#access-code").value = ""; await openWorkspace(); }
  catch (error) { $("#access-error").textContent = error.message; }
  finally { button.disabled = false; }
});
async function bootstrap() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const access = fragment.get("access");
  if (access) {
    history.replaceState(null, "", location.pathname + location.search);
    try { await api("/api/login", jsonOptions("POST", {access_code: access})); }
    catch (error) { $("#access-error").textContent = error.message; return; }
  }
  try { await openWorkspace(); } catch { showAccess(); }
}

$("#logout").addEventListener("click", async () => {
  try { await api("/api/logout", {method: "POST"}); } catch (error) { toast(error.message); return; }
  showAccess();
});
function syncMode() {
  const full = mode() === "full";
  $("#selfie-card").hidden = !full; $("#card-check-row").hidden = !full;
  const count = full ? ($("#card-check").checked ? 10 : 8) : 4;
  $("#request-estimate").textContent = `Tối đa ${count} request API`;
  $("#verify span").textContent = state.busy ? "Đang kiểm tra…" : full ? "Kiểm tra ảnh" : "Đọc giấy tờ";
}
document.querySelectorAll('input[name="mode"]').forEach(input => input.addEventListener("change", syncMode));
$("#card-check").addEventListener("change", syncMode);
function clearImage(slot) {
  if (state.urls[slot]) URL.revokeObjectURL(state.urls[slot]);
  delete state.images[slot]; delete state.urls[slot];
  const card = document.querySelector(`[data-slot="${slot}"]`);
  card.querySelector("img").hidden = true; card.querySelector("img").removeAttribute("src");
  card.querySelector(".empty-preview").hidden = false; card.querySelector(".remove-image").hidden = true;
  card.querySelector(".image-state").textContent = "Chưa có ảnh"; card.querySelector("input").value = "";
}
function selectImage(slot, file) {
  if (!file || state.busy) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) return toast("Dùng ảnh JPG, PNG hoặc WebP.");
  if (file.size > 5 * 1024 * 1024 || file.size === 0) return toast("Ảnh cần có dung lượng từ 1 byte đến 5 MB.");
  clearImage(slot); state.images[slot] = file; state.urls[slot] = URL.createObjectURL(file);
  const card = document.querySelector(`[data-slot="${slot}"]`);
  const image = card.querySelector("img");
  image.onerror = () => { clearImage(slot); toast("Không đọc được ảnh đã chọn."); };
  image.src = state.urls[slot]; image.hidden = false;
  card.querySelector(".empty-preview").hidden = true; card.querySelector(".remove-image").hidden = false;
  card.querySelector(".image-state").textContent = `Đã chọn · ${(file.size / 1024).toFixed(0)} KB`;
  $("#form-error").textContent = "";
}
document.querySelectorAll("[data-file]").forEach(input => input.addEventListener("change", () => selectImage(input.dataset.file, input.files[0])));
document.querySelectorAll(".remove-image").forEach(button => button.addEventListener("click", () => { if (!state.busy) clearImage(button.closest("[data-slot]").dataset.slot); }));
function stopCamera() {
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  state.stream = null; $("#camera-video").srcObject = null;
}
document.querySelectorAll(".camera-open").forEach(button => button.addEventListener("click", async () => {
  if (state.busy) return;
  if (!navigator.mediaDevices?.getUserMedia) return toast("Trình duyệt chưa hỗ trợ camera. Bạn có thể tải ảnh lên.");
  state.slot = button.dataset.camera;
  $("#camera-title").textContent = `Chụp ${labels[state.slot]}`;
  $("#camera-error").textContent = ""; $("#camera-capture").disabled = true;
  $(".camera-stage").classList.toggle("selfie", state.slot === "selfie");
  $("#camera-dialog > .muted").textContent = state.slot === "selfie" ? "Nhìn thẳng vào camera, đủ sáng, không che khuôn mặt." : "Chụp đủ bốn góc giấy tờ, giữ máy ổn định và tránh lóa.";
  $("#camera-dialog").showModal();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: state.slot === "selfie" ? "user" : "environment", width: {ideal: 1920}, height: {ideal: 1080}}, audio: false});
    if (!$("#camera-dialog").open) { stream.getTracks().forEach(t => t.stop()); return; }
    state.stream = stream; $("#camera-video").srcObject = stream; await $("#camera-video").play(); $("#camera-capture").disabled = false;
  } catch { $("#camera-error").textContent = "Chưa mở được camera. Kiểm tra quyền camera hoặc dùng nút tải ảnh."; }
}));
$("#camera-close").addEventListener("click", () => $("#camera-dialog").close());
$("#camera-dialog").addEventListener("close", stopCamera);
$("#camera-capture").addEventListener("click", () => {
  const video = $("#camera-video"); if (!video.videoWidth) return;
  const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob(blob => { if (blob) selectImage(state.slot, new File([blob], `${state.slot}.jpg`, {type: "image/jpeg"})); $("#camera-dialog").close(); }, "image/jpeg", .92);
});
document.addEventListener("visibilitychange", () => { if (document.hidden && $("#camera-dialog").open) $("#camera-dialog").close(); });
window.addEventListener("pagehide", stopCamera);
function setBusy(value) {
  state.busy = value;
  document.querySelectorAll('.input-panel button, .input-panel input').forEach(e => e.disabled = value);
  $("#verify").disabled = value || !state.providerReady;
  $("#verify span").textContent = value ? "Đang kiểm tra…" : mode() === "ocr" ? "Đọc giấy tờ" : "Kiểm tra ảnh";
  syncSdkControls();
}

function selectFlow(flow) {
  state.flow = flow;
  for (const kind of Object.keys(sdkFlows)) $(`#${kind}-panel`).hidden = flow !== kind;
  $("#image-input-panel").hidden = flow !== "images";
  for (const [kind, panel] of Object.entries(resultPanels)) panel.hidden = flow !== kind || !state.results[kind];
  for (const kind of Object.keys(resultPanels)) {
    const button = $(`#flow-${kind}`);
    button.classList.toggle("selected", flow === kind);
    button.setAttribute("aria-pressed", String(flow === kind));
  }
  const tab = sdkFlows[flow]?.tab;
  history.replaceState(null, "", location.pathname + (tab ? `?tab=${tab}` : ""));
}
for (const flow of Object.keys(resultPanels)) $("#flow-" + flow).addEventListener("click", () => {
  if (state.sdkStarting) return;
  selectFlow(flow);
});
function syncSdkControls() {
  const occupied = state.busy || Boolean(state.sdkRun) || state.sdkStarting;
  for (const flow of Object.keys(resultPanels)) $(`#flow-${flow}`).disabled = state.sdkStarting;
  $("#verify").disabled = occupied || !state.providerReady;
  for (const flow of Object.keys(sdkFlows)) {
    const controls = sdkControls(flow);
    const active = Boolean(state.sdkRun) && state.sdkRunFlow === flow;
    controls.start.disabled = occupied || !state.providerReady;
    controls.start.hidden = active;
    controls.stop.hidden = !active;
    controls.consent.disabled = occupied;
  }
}
async function finishSdk() {
  const id = state.sdkRun;
  if (!id) return;
  const controls = sdkControls(state.sdkRunFlow);
  controls.stop.disabled = true;
  try {
    let result = await api(`/api/sdk/runs/${id}/finish`, jsonOptions("POST", {}));
    while (result.status === "running" && state.sdkRun === id) {
      renderResult(result);
      controls.status.textContent = "Đã tắt camera. Đang chờ các request đã gửi đến VNPT trả kết quả…";
      await new Promise(resolve => setTimeout(resolve, 1200));
      result = await api(`/api/sdk/runs/${id}`);
    }
    state.sdkRun = null; state.sdkRunFlow = null; renderResult(result);
    controls.status.textContent = "Đã tắt camera. Kết quả bên dưới là các phản hồi thực tế backend nhận từ VNPT.";
  } catch (error) { controls.error.textContent = error.message; }
  finally { controls.stop.disabled = false; syncSdkControls(); }
}
async function startSdk(flow) {
  const controls = sdkControls(flow);
  controls.error.textContent = "";
  if (!controls.consent.checked) {
    controls.error.textContent = flow === "full" ? "Cần đồng ý gửi giấy tờ và dữ liệu camera đến VNPT trước khi kiểm tra." : "Cần đồng ý gửi dữ liệu camera đến VNPT trước khi kiểm tra.";
    return;
  }
  state.sdkStarting = true; syncSdkControls();
  try {
    const run = await api("/api/sdk/runs", jsonOptions("POST", {consent: true, flow: sdkFlows[flow].requestFlow}));
    state.sdkRun = run.id; state.sdkRunFlow = flow;
    controls.status.textContent = "Đang mở màn hình camera của VNPT Web SDK…";
    location.assign(`/sdk#run=${run.id}&tab=${sdkFlows[flow].tab}`);
  } catch (error) { controls.error.textContent = error.message; }
  finally { state.sdkStarting = false; syncSdkControls(); }
}
for (const flow of Object.keys(sdkFlows)) {
  sdkControls(flow).start.addEventListener("click", () => startSdk(flow));
  sdkControls(flow).stop.addEventListener("click", finishSdk);
}
$("#verify").addEventListener("click", async () => {
  $("#form-error").textContent = "";
  if (!state.providerReady) { $("#form-error").textContent = state.providerMessage; return; }
  const required = mode() === "full" ? ["front", "back", "selfie"] : ["front", "back"];
  const missing = required.filter(slot => !state.images[slot]);
  if (missing.length) { $("#form-error").textContent = `Cần thêm ảnh ${missing.map(s => labels[s]).join(", ")}.`; return; }
  if (!$("#consent").checked) { $("#form-error").textContent = "Cần đồng ý gửi ảnh đến VNPT trước khi kiểm tra."; return; }
  const data = new FormData(); data.set("mode", mode()); data.set("consent", "yes"); data.set("card_check", String($("#card-check").checked));
  required.forEach(slot => data.set(slot, state.images[slot]));
  setBusy(true);
  try {
    const job = await api("/api/verify", {method: "POST", body: data});
    $("#result-panel").hidden = false; $("#result-panel").scrollIntoView({behavior: "smooth", block: "start"});
    await pollJob(job.id);
  } catch (error) { $("#form-error").textContent = error.message; setBusy(false); }
});
function renderResult(result) {
  state.result = result;
  const flow = resultFlow(result);
  state.results[flow] = result;
  const panel = resultPanels[flow];
  const get = id => panel.querySelector(`#${sdkFlows[flow]?.prefix || ""}${id}`);
  panel.hidden = state.flow !== flow;
  panel.querySelector("h2").textContent = sdkFlows[flow]?.title || (result.mode === "ocr" ? "Kết quả OCR" : "Kết quả kiểm tra ảnh");
  get("progress-text").textContent = result.progress;
  get("calls").textContent = `${result.calls} request đã gửi đến VNPT`;
  panel.querySelector(".spinner").hidden = result.status !== "running";
  get("result-error").hidden = !result.error; get("result-error").textContent = result.error || "";
  const badge = get("result-badge");
  const states = result.checks.map(check => check.status);
  const summary = result.status === "running" ? ["running", "Đang xử lý"] : result.status === "error" ? ["error", "Chưa hoàn tất"] : states.includes("fail") ? ["fail", "Có bước chưa đạt"] : !states.length || states.includes("review") ? ["review", "Cần xem lại"] : ["pass", "Các bước đã chạy đạt"];
  badge.className = `badge ${summary[0]}`; badge.textContent = summary[1];
  get("check-results").replaceChildren(...result.checks.map(check => {
    const row = document.createElement("div"); row.className = `check-row ${check.status}`;
    const icon = document.createElement("span"); icon.textContent = check.status === "pass" ? "✓" : check.status === "fail" ? "×" : "!";
    const content = document.createElement("div"); const strong = document.createElement("strong"); strong.textContent = check.label; const p = document.createElement("p"); p.textContent = check.message; content.append(strong, p);
    const status = document.createElement("small"); status.textContent = {pass: "Đạt", fail: "Chưa đạt", review: "Xem lại"}[check.status] || "Chưa rõ";
    row.append(icon, content, status); return row;
  }));
  get("extracted").hidden = !result.fields.length;
  get("extracted-fields").replaceChildren(...result.fields.map(field => {
    const item = document.createElement("div"); const label = document.createElement("dt"); label.textContent = field.label; const value = document.createElement("dd"); value.textContent = field.value; item.append(label, value); return item;
  }));
  const requests = result.requests || [];
  get("request-log").hidden = !requests.length;
  get("request-rows").replaceChildren(...requests.map(record => {
    const row = document.createElement("tr");
    const name = record.endpoint.endsWith("addFile") ? "Upload tệp" : record.endpoint.endsWith("liveness-3d") ? "Liveness Oval" : record.endpoint.endsWith("/mask") ? "Kiểm tra che mặt" : record.endpoint.endsWith("/compare") ? "So khớp với CCCD" : record.endpoint.includes("/ocr/") ? "Đọc giấy tờ (OCR)" : record.endpoint.includes("/card/") ? "Kiểm tra giấy tờ" : record.endpoint;
    const values = [name, record.http_status ?? "—", record.gateway_status ?? "—", record.error_kind || record.code || record.status || "Đang gửi", record.elapsed_ms == null ? "—" : `${record.elapsed_ms} ms`];
    for (const value of values) { const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell); }
    row.title = record.endpoint; return row;
  }));
  get("raw-result").textContent = JSON.stringify({results: result.raw, requests}, null, 2);
  get("download-result").disabled = result.status === "running";
}
async function pollJob(id, failures = 0) {
  clearTimeout(state.poll);
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(id)}`); renderResult(result); setBusy(result.status === "running");
    if (result.status === "running") state.poll = setTimeout(() => pollJob(id), 1200);
  } catch (error) {
    if (state.csrf && failures < 4) { state.poll = setTimeout(() => pollJob(id, failures + 1), 2500); return; }
    setBusy(false); $("#result-error").textContent = `${error.message} Nếu đã bắt đầu kiểm tra, đừng gửi lại ngay vì có thể phát sinh thêm request.`; $("#result-error").hidden = false; $("#result-panel .spinner").hidden = true;
  }
}
for (const flow of Object.keys(resultPanels)) {
  resultPanels[flow].querySelector(`#${sdkFlows[flow]?.prefix || ""}download-result`).addEventListener("click", () => {
    const result = state.results[flow];
    if (!result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: "application/json"}));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `ekyc-${result.mode}-${result.id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
syncMode();
bootstrap();
