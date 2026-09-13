import { provideHttpClient, withFetch } from '@angular/common/http';
import { type ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';

// Zoneless change detection (the Angular default): signal updates coming from WebSocket
// callbacks schedule rendering directly, without zone.js patching every async API.
export const appConfig: ApplicationConfig = {
  providers: [provideBrowserGlobalErrorListeners(), provideHttpClient(withFetch())],
};
