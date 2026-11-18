import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.js';
import { EnvironmentLabel } from './components/EnvironmentLabel.js';
import { createIdentityLinks } from './lib/identityLinks.js';
import './styles.css';

let storage: Storage | undefined;
try {
  storage = window.sessionStorage;
} catch {
  /* Private browsers can disable storage. */
}
const identityLinks = createIdentityLinks(storage);
const initialPage = identityLinks.capture(window.location, window.history);
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App identityLinks={identityLinks} initialPage={initialPage} />
    <EnvironmentLabel />
  </StrictMode>,
);
