(function injectDoubaoAudioCapture() {
  if (document.documentElement.dataset.doubaoAudioCaptureInjected === "1") {
    return;
  }
  document.documentElement.dataset.doubaoAudioCaptureInjected = "1";

  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("injected.js");
  script.async = false;
  script.onload = () => script.remove();
  (document.documentElement || document.head || document.body).appendChild(script);
})();
