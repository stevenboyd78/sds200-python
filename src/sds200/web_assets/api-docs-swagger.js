"use strict";

window.ui = SwaggerUIBundle({
  url: "openapi.json",
  dom_id: "#swagger-ui",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  validatorUrl: null,
  presets: [
    SwaggerUIBundle.presets.apis,
    SwaggerUIBundle.SwaggerUIStandalonePreset,
  ],
});
