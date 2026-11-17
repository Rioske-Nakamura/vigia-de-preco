if (location.origin === "http://127.0.0.1:8765") {
  window.addEventListener("message", event => {
    if (event.source === window && event.origin === location.origin && event.data === "vigia-consultar") {
      chrome.runtime.sendMessage("consultar").catch(() => {});
    }
  });
  chrome.runtime.sendMessage("consultar").catch(() => {});
}
