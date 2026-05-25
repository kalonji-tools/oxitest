// Click-to-zoom for rendered Mermaid SVGs.
// Waits for Mermaid to replace <pre class="mermaid"> with <svg>.
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    new MutationObserver(function () {
      document.querySelectorAll("svg[id^='mermaid-']:not(.zoom-ready)").forEach(function (svg) {
        svg.classList.add("zoom-ready");

        var wrapper = document.createElement("div");
        wrapper.className = "mermaid-zoom";
        svg.parentNode.insertBefore(wrapper, svg);
        wrapper.appendChild(svg);

        var hint = document.createElement("div");
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
    }).observe(document.body, { childList: true, subtree: true });
  });
})();
