"use strict";
(() => {
  const runId = new URLSearchParams(location.hash.slice(1)).get("run");
  let csrf = "";
  let finishing = false;
  const button = document.querySelector("#sdk-finish");
  const loading = document.querySelector("#sdk-loading");
  const errorText = document.querySelector("#sdk-error");
  function stopCamera() {
    document.querySelectorAll("video").forEach(video => {
      const stream = video.srcObject;
      if (stream && typeof stream.getTracks === "function") stream.getTracks().forEach(track => track.stop());
      video.srcObject = null;
    });
  }
  function showError(message) { errorText.hidden = false; errorText.textContent = message; }
  async function request(path, options = {}) {
    const response = await fetch(path, {credentials: "same-origin", ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không hoàn tất được yêu cầu. Vui lòng quay lại eKYC Lab.");
    return data;
  }
  async function finish() {
    if (finishing || !csrf) return;
    finishing = true; button.disabled = true; button.textContent = "Đang nhận kết quả…";
    document.body.classList.add("finishing"); stopCamera();
    try {
      let result = await request(`/api/sdk/runs/${runId}/finish`, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: "{}"});
      while (result.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 1200));
        result = await request(`/api/sdk/runs/${runId}`);
      }
      location.replace("/");
    } catch (error) {
      showError(error.message); finishing = false; button.disabled = false;
      button.textContent = "Kết thúc và xem kết quả";
    }
  }
  button.addEventListener("click", finish);
  document.querySelector(".sdk-brand").addEventListener("click", event => {
    if (csrf) { event.preventDefault(); finish(); }
  });
  window.addEventListener("pagehide", stopCamera);
  window.addEventListener("error", event => {
    if (!event.filename?.startsWith("chrome-extension://")) showError("Web SDK gặp lỗi xử lý camera. Bạn có thể kết thúc lượt để xem những bước đã nhận được.");
  });
  window.addEventListener("unhandledrejection", () => showError("Web SDK chưa hoàn tất xử lý. Kiểm tra quyền camera hoặc kết thúc lượt để xem kết quả từng bước."));
  async function start() {
    if (!/^[a-f0-9]{32}$/.test(runId || "")) throw new Error("Thiếu mã lượt Web SDK hợp lệ. Vui lòng quay lại eKYC Lab.");
    const config = await request(`/api/sdk/runs/${runId}/config`);
    csrf = config.HEADERS_REQUEST["X-CSRF-Token"]; button.disabled = false;
    if (!window.SDK?.launch || !window.FaceVNPTBrowserSDK) throw new Error("Chưa tải được bộ thư viện VNPT Web SDK.");
    // Callback only signals completion; results come from server-observed VNPT responses.
    window.SDK.launch({...config, CALL_BACK_END_FLOW: finish});
    loading.hidden = true;
  }
  start().catch(error => { loading.hidden = true; showError(error.message); });
})();
