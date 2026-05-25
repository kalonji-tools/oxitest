// Mermaid diagram enhancements: click-to-zoom + dark mode fill swap.
(function () {
  // Light → dark fill mapping for our style directives.
  var FILL_MAP = {
    "#f5f5f5": "#2d2d2d",
    "#fef3e2": "#3d2800",
    "#e8f4fd": "#0d2137",
    "#fff3e0": "#3d2200",
    "#e3f2fd": "#0a1929",
    "#fff9c4": "#3d3400",
    "#f3e5f5": "#2d1f33"
  };

  function isDark() {
    return document.body.getAttribute("data-md-color-scheme") === "slate";
  }

  // Swap fills on all styled rects/polygons in rendered Mermaid SVGs.
  function applyFills() {
    var dark = isDark();
    document.querySelectorAll("svg[id^='mermaid-'] rect, svg[id^='mermaid-'] polygon").forEach(function (el) {
      var style = el.getAttribute("style") || "";
      if (!style.includes("fill:")) return;

      Object.keys(FILL_MAP).forEach(function (light) {
        var target = dark ? FILL_MAP[light] : light;
        var other = dark ? light : FILL_MAP[light];
        if (style.includes("fill:" + other)) {
          el.setAttribute("style", style.replace("fill:" + other, "fill:" + target));
        }
      });
    });
  }

  // Zoom: wrap rendered SVGs in a clickable container.
  function wrapForZoom() {
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
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Watch for Mermaid rendering SVGs.
    var svgObserver = new MutationObserver(function () {
      wrapForZoom();
      applyFills();
    });
    svgObserver.observe(document.body, { childList: true, subtree: true });

    // Watch for color scheme toggle.
    var schemeObserver = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.attributeName === "data-md-color-scheme") {
          applyFills();
        }
      });
    });
    schemeObserver.observe(document.body, { attributes: true });
  });
})();
