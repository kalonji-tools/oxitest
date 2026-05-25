// Wrap each mermaid diagram in a zoomable container
document.addEventListener("DOMContentLoaded", function () {
  // MutationObserver waits for mermaid to render SVGs
  const observer = new MutationObserver(function () {
    document.querySelectorAll("pre.mermaid:not(.zoom-ready)").forEach(function (pre) {
      pre.classList.add("zoom-ready");

      const wrapper = document.createElement("div");
      wrapper.className = "mermaid-zoom";
      pre.parentNode.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);

      const hint = document.createElement("div");
      hint.className = "mermaid-zoom-hint";
      hint.textContent = "Click to zoom";
      wrapper.appendChild(hint);

      wrapper.addEventListener("click", function () {
        wrapper.classList.toggle("zoomed");
        hint.textContent = wrapper.classList.contains("zoomed")
          ? "Click to close"
          : "Click to zoom";
      });
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
});
