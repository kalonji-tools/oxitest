/* docs/user/assets/nav.js — Injects navigation bar into oxitest docs */
(function () {
  "use strict";

  // Determine which section is active based on URL path
  var path = window.location.pathname;
  var section = "home";
  if (path.includes("/internals/architecture-map")) section = "map";
  else if (path.includes("/internals/")) section = "internals";
  else if (
    path.includes("/how-to/") ||
    path.includes("/tutorials/") ||
    path.includes("/reference/") ||
    path.includes("/explanation/")
  )
    section = "user";

  // Auto-detect GitHub Pages prefix from URL
  var prefix = window.location.pathname.match(/^\/oxitest/) ? "/oxitest" : "";
  var basePaths = window.oxiNavPaths || {
    home: prefix + "/",
    user: prefix + "/site/",
    internals: prefix + "/internals/book/",
    map: prefix + "/internals/architecture-map.html",
    github: "https://github.com/kalonji-tools/oxitest",
  };

  function cls(name, isActive) {
    return name + (isActive ? " active" : "");
  }

  var nav = document.createElement("div");
  nav.className = "oxi-nav";
  nav.innerHTML =
    '<a class="oxi-nav-brand" href="' +
    basePaths.home +
    '"><span>oxi</span>test</a>' +
    '<a class="' +
    cls("", section === "user") +
    '" href="' +
    basePaths.user +
    '">User Guide</a>' +
    '<a class="' +
    cls("", section === "internals") +
    '" href="' +
    basePaths.internals +
    '">Internals</a>' +
    '<a class="' +
    cls("", section === "map") +
    '" href="' +
    basePaths.map +
    '">Architecture Map</a>' +
    '<div class="oxi-nav-spacer"></div>' +
    '<a class="oxi-nav-github" href="' +
    basePaths.github +
    '" title="GitHub">' +
    '<svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>' +
    "</a>";

  // Insert at the very top of body, before everything else
  document.body.insertBefore(nav, document.body.firstChild);

  // Footer
  var footer = document.createElement("div");
  footer.className = "oxi-footer";
  footer.innerHTML =
    '<div class="oxi-footer-links">' +
    '<a href="' + basePaths.user + '">User Guide</a>' +
    '<a href="' + basePaths.internals + '">Internals</a>' +
    '<a href="' + basePaths.map + '">Architecture Map</a>' +
    '<a href="' + basePaths.github + '">GitHub</a>' +
    "</div>" +
    "<div>oxitest &mdash; A fast, typed Python test framework backed by Rust</div>";
  document.body.appendChild(footer);
})();
