"use strict";
window.nfcLab = (() => {
  const node = id => document.getElementById("nfc-" + id);
  const statuses = {creating: "Đang tạo phiên", pending: "Chờ quét trong app", processing: "Signicat đang xử lý", accepted: "Signicat chấp nhận · Sandbox", rejected: "Signicat từ chối", inconclusive: "Chưa đủ kết luận", canceled: "Đã hủy", failed: "Lượt quét thất bại", error: "Chưa tạo được phiên"};
  let ready = false, busy = false, run = null, active = false, generation = 0;
  const terminal = () => run && !["creating", "pending", "processing"].includes(run.status);
  function sync() {
    node("start").disabled = !ready || busy || Boolean(run && !terminal());
    node("start").hidden = Boolean(run && !terminal());
    node("start").textContent = terminal() ? "Tạo phiên mới" : "Tạo phiên NFC";
    node("consent").disabled = busy || Boolean(run && !terminal());
    node("open").hidden = !run?.capture_url;
    if (run?.capture_url) node("open").href = run.capture_url;
    else node("open").removeAttribute("href");
    node("refresh").hidden = !run || terminal();
    node("refresh").disabled = busy;
    node("progress").textContent = busy ? "Đang chờ phản hồi Signicat…" : run ? statuses[run.status] || "Chưa rõ trạng thái" : "";
    node("result").hidden = !active || !run;
  }
  function textElement(tag, text) { const element = document.createElement(tag); element.textContent = text; return element; }
  function render(value) {
    run = value;
    node("error").textContent = run?.error || "";
    node("checks").replaceChildren(); node("fields").replaceChildren(); node("log").replaceChildren();
    if (!run) { node("json").textContent = ""; sync(); return; }
    node("state").textContent = statuses[run.status] || "Chưa rõ trạng thái";
    for (const check of run.checks || []) {
      const row = textElement("div", ""); row.className = "check-row " + check.state;
      row.append(textElement("span", check.state === "pass" ? "✓" : check.state === "fail" ? "×" : "?"));
      const detail = textElement("div", ""); detail.append(textElement("strong", check.label), textElement("p", check.value)); row.append(detail); node("checks").append(row);
    }
    for (const field of run.fields || []) { const cell = textElement("div", ""); cell.append(textElement("dt", field.label), textElement("dd", field.value)); node("fields").append(cell); }
    node("fields").hidden = !run.fields?.length;
    for (const entry of run.log || []) { const row = textElement("tr", ""); for (const value of [entry.label, entry.http ?? "—", entry.duration_ms + " ms"]) row.append(textElement("td", value)); node("log").append(row); }
    node("calls").textContent = `${run.calls} request từ Lab. Số này không bao gồm các request app ReadID gửi và không phải số lượt tính phí.`;
    node("json").textContent = JSON.stringify(run.raw || {}, null, 2);
    sync();
  }
  async function request(action) {
    const version = generation;
    busy = true; node("error").textContent = ""; sync();
    try { const result = await action(); if (version === generation) render(result); }
    catch (error) { if (version === generation) node("error").textContent = error.message; }
    finally { if (version === generation) { busy = false; sync(); } }
  }
  node("start").addEventListener("click", () => {
    if (busy || !ready) return;
    if (!node("consent").checked) { node("error").textContent = "Cần đồng ý chuyển sang Signicat trước khi tạo phiên."; return; }
    request(() => api("/api/nfc/runs", jsonOptions("POST", {consent: true})));
  });
  node("refresh").addEventListener("click", () => {
    if (!run || busy || terminal()) return;
    request(() => api(`/api/nfc/runs/${encodeURIComponent(run.id)}/refresh`, jsonOptions("POST", {})));
  });
  node("download").addEventListener("click", () => {
    if (!run) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = `ekyc-nfc-${run.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return {
    async load() {
      const version = generation;
      try {
        const data = await api("/api/nfc/status");
        if (version !== generation) return;
        ready = data.ready; node("connection").textContent = data.message; render(data.latest_run);
      } catch (error) { if (version === generation) { ready = false; node("connection").textContent = error.message; sync(); } }
    },
    select(flow) {
      active = flow === "nfc";
      node("panel").hidden = !active;
      const button = document.getElementById("flow-nfc"); button.classList.toggle("selected", active); button.setAttribute("aria-pressed", String(active));
      sync();
    },
    clear() { generation++; ready = false; busy = false; node("consent").checked = false; render(null); }
  };
})();
