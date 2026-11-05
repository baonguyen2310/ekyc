"use strict";
window.vnptServices = (() => {
  const services = ["idcheck", "vneid"];
  const node = (service, suffix) => document.getElementById(`${service}-${suffix}`);
  let generation = 0;

  function textElement(tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
  }

  function unavailable(service, message) {
    node(service, "connection").textContent = message;
    node(service, "requirements").replaceChildren();
  }

  return {
    async load() {
      const version = generation;
      try {
        // This endpoint only reads Lab's implementation state. It never calls VNPT.
        const data = await api("/api/vnpt-services/status");
        if (version !== generation) return;
        for (const service of services) {
          const status = data[service];
          unavailable(service, status?.message || "Chưa lấy được trạng thái kết nối.");
          for (const requirement of status?.requirements || []) {
            const item = textElement("li", "");
            item.append(textElement("strong", requirement.label), textElement("p", requirement.detail));
            node(service, "requirements").append(item);
          }
        }
      } catch (error) {
        if (version !== generation) return;
        for (const service of services) unavailable(service, error.message);
      }
    },
    select(flow) {
      for (const service of services) {
        const active = service === flow;
        node(service, "panel").hidden = !active;
        const button = document.getElementById(`flow-${service}`);
        button.classList.toggle("selected", active);
        button.setAttribute("aria-pressed", String(active));
      }
    },
    clear() {
      generation++;
      for (const service of services) unavailable(service, "Đang đọc trạng thái kết nối…");
    }
  };
})();
