// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

(() => {
    const darkThemes = ['ayu', 'navy', 'coal'];
    const lightThemes = ['light', 'rust'];

    const classList = document.getElementsByTagName('html')[0].classList;

    let lastThemeWasLight = true;
    for (const cssClass of classList) {
        if (darkThemes.includes(cssClass)) {
            lastThemeWasLight = false;
            break;
        }
    }

    const theme = lastThemeWasLight ? 'default' : 'dark';
    mermaid.initialize({ startOnLoad: true, theme, maxTextSize: 90000 });

    // Click-to-fullscreen for mermaid diagrams
    const style = document.createElement('style');
    style.textContent = `
        .mermaid-fullscreen-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0,0,0,0.85); z-index: 9999;
            display: flex; align-items: center; justify-content: center;
            cursor: zoom-out;
        }
        .mermaid-fullscreen-overlay svg {
            max-width: 95vw; max-height: 95vh;
        }
        .mermaid { cursor: zoom-in; }
    `;
    document.head.appendChild(style);

    document.addEventListener('click', (e) => {
        const overlay = e.target.closest('.mermaid-fullscreen-overlay');
        if (overlay) { overlay.remove(); return; }
        const diagram = e.target.closest('.mermaid');
        if (!diagram) return;
        const div = document.createElement('div');
        div.className = 'mermaid-fullscreen-overlay';
        div.innerHTML = diagram.innerHTML;
        document.body.appendChild(div);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const overlay = document.querySelector('.mermaid-fullscreen-overlay');
            if (overlay) overlay.remove();
        }
    });

    // Simplest way to make mermaid re-render the diagrams in the new theme is via refreshing the page

    for (const darkTheme of darkThemes) {
        document.getElementById(darkTheme).addEventListener('click', () => {
            if (lastThemeWasLight) {
                window.location.reload();
            }
        });
    }

    for (const lightTheme of lightThemes) {
        document.getElementById(lightTheme).addEventListener('click', () => {
            if (!lastThemeWasLight) {
                window.location.reload();
            }
        });
    }
})();
