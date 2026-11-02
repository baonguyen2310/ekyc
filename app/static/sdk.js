"use strict";
(() => {
  const runId = new URLSearchParams(location.hash.slice(1)).get("run");
  const nativeFetch = window.fetch.bind(window);
  const proxyPrefix = `/api/sdk/runs/${runId}/proxy/`;
  let csrf = "", interrupted = false, closed = false, closePromise = null, pollTimer = null, clientError = null;
  const button = document.querySelector("#sdk-finish");
  const loading = document.querySelector("#sdk-loading");
  const progress = document.querySelector("#sdk-progress");
  const failure = document.querySelector("#sdk-failure");
  function stopCamera() {
    document.querySelectorAll("video").forEach(video => {
      if (video.srcObject?.getTracks) video.srcObject.getTracks().forEach(track => track.stop());
      if (typeof video.webkitExitFullscreen === "function") { try { video.webkitExitFullscreen(); } catch {} }
      video.srcObject = null;
    });
  }
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await nativeFetch(path, {credentials: "same-origin", ...options, signal: controller.signal});
      let data;
      try { data = await response.json(); }
      catch { throw new Error(`Lab trả phản hồi không đọc được (HTTP ${response.status}).`); }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Yêu cầu thất bại (HTTP ${response.status}).`);
      return data;
    } finally { clearTimeout(timer); }
  }
  function closeRun() {
    if (closePromise) return closePromise;
    clearTimeout(pollTimer); stopCamera(); button.disabled = true;
    document.body.classList.add("finishing");
    closePromise = (async () => {
      let result = await request(`/api/sdk/runs/${runId}/finish`, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify({client_error: clientError})});
      while (result.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 1200));
        result = await request(`/api/sdk/runs/${runId}`);
      }
      closed = true;
      return result;
    })().catch(error => { closePromise = null; button.disabled = false; throw error; });
    return closePromise;
  }
  async function finish() {
    if (!csrf) { location.replace("/?tab=oval"); return; }
    try { await closeRun(); location.replace("/?tab=oval"); }
    catch { location.replace("/?tab=oval"); }
  }
  function interrupt(message, kind = "sdk_runtime") {
    if (interrupted) return;
    interrupted = true; clientError = kind; clearTimeout(pollTimer); stopCamera();
    loading.hidden = true; document.body.classList.add("sdk-interrupted");
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
    else if (document.webkitFullscreenElement && document.webkitExitFullscreen) { try { document.webkitExitFullscreen(); } catch {} }
    document.querySelector("#sdk-failure-message").textContent = message;
    document.querySelector("#sdk-failure-summary").textContent = "Đã dừng camera. Đang nhận trạng thái các request đã gửi…";
    failure.hidden = false;
    document.body.append(failure);
    failure.showModal();
    if (csrf) closeRun().then(result => {
      document.querySelector("#sdk-failure-summary").textContent = `Đã kết thúc lượt: ${result.calls} request gửi đến VNPT. Các bước đã nhận được giữ trong phần Kết quả Liveness Oval.`;
    }).catch(error => { document.querySelector("#sdk-failure-summary").textContent = error.message; });
    else document.querySelector("#sdk-failure-summary").textContent = "Quay lại Lab để mở phiên thử mới.";
  }
  async function poll() {
    try {
      const result = await request(`/api/sdk/runs/${runId}`);
      if (closed || interrupted) return;
      const last = result.requests?.at(-1);
      progress.textContent = `${result.calls} request đã gửi${last?.status === "pending" ? " · Đang chờ VNPT" : ""}`;
      if (result.error) { interrupt(result.error); return; }
      pollTimer = setTimeout(poll, 1800);
    } catch (error) { interrupt(error.message); }
  }
  // Inspect only this SDK run's gateway requests. Credentials/config/status use nativeFetch.
  window.fetch = async function(input, options) {
    const url = new URL(typeof input === "string" ? input : input.url, location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith(proxyPrefix)) return nativeFetch(input, options);
    if (interrupted || closed) return new Response(JSON.stringify({error: "Lượt thử đã dừng."}), {status: 409, headers: {"Content-Type": "application/json"}});
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await nativeFetch(input, {...options, signal: controller.signal});
      let payload;
      try { payload = await response.clone().json(); }
      catch { interrupt(`Gateway trả phản hồi không đọc được (HTTP ${response.status}).`, "gateway_response"); return response; }
      if (!response.ok || payload.error || payload.message !== "IDG-00000000") {
        interrupt(payload.detail || payload.error || `VNPT chưa hoàn tất bước này (HTTP ${response.status}, mã ${payload.message || "không rõ"}).`, "gateway_response");
      }
      return response;
    } catch (error) {
      interrupt(error.name === "AbortError" ? "Request vượt thời gian chờ. Đã dừng lượt thử; không tự gửi lại để tránh tốn hạn mức." : "Kết nối bị gián đoạn trong lúc gửi dữ liệu. Đã dừng lượt thử, hãy xem các bước đã nhận được.", "network");
      throw error;
    } finally { clearTimeout(timeout); }
  };
  button.addEventListener("click", finish);
  document.querySelector("#sdk-view-results").addEventListener("click", finish);
  failure.addEventListener("cancel", event => event.preventDefault());
  document.querySelector(".sdk-brand").addEventListener("click", event => { event.preventDefault(); finish(); });
  window.addEventListener("pagehide", () => { clearTimeout(pollTimer); stopCamera(); });
  window.addEventListener("error", event => {
    if (!event.filename?.startsWith("chrome-extension://")) interrupt("SDK gặp lỗi xử lý camera. Camera đã dừng; bạn có thể xem các bước đã hoàn tất trong kết quả.");
  });
  window.addEventListener("unhandledrejection", event => {
    const stack = String(event.reason?.stack || "");
    if (stack.includes("chrome-extension://") && !stack.includes(location.origin + "/static/")) return;
    interrupt("SDK không hoàn tất xử lý dữ liệu quét. Camera đã dừng; các kết quả đã nhận được vẫn được giữ.");
  });
  async function start() {
    if (!/^[a-f0-9]{32}$/.test(runId || "")) throw new Error("Thiếu mã lượt thử hợp lệ. Vui lòng quay lại Lab.");
    const config = await request(`/api/sdk/runs/${runId}/config`);
    csrf = config.HEADERS_REQUEST["X-CSRF-Token"]; button.disabled = false;
    if (!window.SDK?.launch || !window.FaceVNPTBrowserSDK) throw new Error("Chưa tải được thư viện VNPT Web SDK.");
    window.SDK.launch({...config, CALL_BACK_END_FLOW: () => { if (!interrupted) finish(); }});
    loading.hidden = true;
    poll();
  }
  start().catch(error => interrupt(error.message));
})();
