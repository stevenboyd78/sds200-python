"use strict";

(() => {
  const STORAGE_KEY = "sdsctl.web.theme";
  const THEMES = Object.freeze([
    "system",
    "lcars",
    "matrix",
    "first-responder",
    "amateur-radio",
  ]);
  const THEME_COLORS = Object.freeze({
    lcars: "#0b0910",
    matrix: "#020705",
    "first-responder": "#07111f",
    "amateur-radio": "#11100c",
  });
  const systemColorQuery =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  let activeTheme = "system";

  function normalizeTheme(value) {
    return THEMES.includes(value) ? value : "system";
  }

  function readStoredTheme() {
    try {
      return normalizeTheme(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      return "system";
    }
  }

  function systemThemeColor() {
    return systemColorQuery !== null && systemColorQuery.matches
      ? "#0d1420"
      : "#eef2f7";
  }

  function updateMetadata(theme) {
    const colorScheme = document.querySelector('meta[name="color-scheme"]');
    const themeColor = document.querySelector('meta[name="theme-color"]');

    if (colorScheme !== null) {
      colorScheme.content = theme === "system" ? "light dark" : "dark";
    }
    if (themeColor !== null) {
      themeColor.content =
        theme === "system" ? systemThemeColor() : THEME_COLORS[theme];
    }
  }

  function applyTheme(value, persist) {
    const theme = normalizeTheme(value);
    activeTheme = theme;
    document.documentElement.dataset.theme = theme;
    updateMetadata(theme);

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // Browser-local persistence is optional; applying the theme still succeeds.
      }
    }

    return theme;
  }

  activeTheme = applyTheme(readStoredTheme(), false);

  if (
    systemColorQuery !== null &&
    typeof systemColorQuery.addEventListener === "function"
  ) {
    systemColorQuery.addEventListener("change", () => {
      if (activeTheme === "system") {
        updateMetadata(activeTheme);
      }
    });
  }

  window.sdsctlTheme = Object.freeze({
    choices: THEMES,
    current: () => activeTheme,
    select: (value) => applyTheme(value, true),
  });
})();
