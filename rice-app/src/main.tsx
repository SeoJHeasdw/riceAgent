import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import '@/styles/globals.css';
import { applyStoredAppearance } from './lib/appearance';
import { markDemoDocument } from './lib/demo';

applyStoredAppearance();
markDemoDocument();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Made with Bob
