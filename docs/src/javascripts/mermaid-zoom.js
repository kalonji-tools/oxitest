// Click-to-zoom for rendered Mermaid SVGs.
// Waits for Mermaid to replace <pre class="mermaid"> with <svg>.
(function () {
  document.addEventListener("DOMContentLoaded", function () {
    new MutationObserver(function () {
      document.querySelectorAll(".mermaid svg:not(.zoom-ready), svg[id^='mermaid-']:not(.zoom-ready)").forEach(function (svg) {
        svg.classList.add("zoom-ready");

        // Wrap the .mermaid container (not just the SVG) so layout stays clean.
        var container = svg.closest(".mermaid") || svg;
        if (container.parentElement && container.parentElement.classList.contains("mermaid-zoom")) return;

        var wrapper = document.createElement("div");
        wrapper.className = "mermaid-zoom";
        container.parentNode.insertBefore(wrapper, container);
        wrapper.appendChild(container);

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
