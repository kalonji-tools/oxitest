// Wrap rendered mermaid SVGs in a zoomable container.
// Waits for Mermaid to replace <pre class="mermaid"> with <svg> before wrapping.
document.addEventListener("DOMContentLoaded", function () {
  var observer = new MutationObserver(function () {
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
  });

  observer.observe(document.body, { childList: true, subtree: true });
});
