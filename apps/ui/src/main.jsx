import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';

import './index.css';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import Configure from './pages/Configure';
import RunTests from './pages/RunTests';
import Results from './pages/Results';
import Attractors from './pages/Attractors';
import History from './pages/History';

function App() {
  return (
    <BrowserRouter>
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: { borderRadius: '10px', background: '#1e293b', color: '#f8fafc', fontSize: '14px' },
        }}
      />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="configure" element={<Configure />} />
          <Route path="run" element={<RunTests />} />
          <Route path="results" element={<Results />} />
          <Route path="attractors" element={<Attractors />} />
          <Route path="history" element={<History />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
