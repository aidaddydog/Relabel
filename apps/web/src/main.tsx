
import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate, Link } from 'react-router-dom'
import App from './pages/App'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Files from './pages/Files'
import UploadOrders from './pages/UploadOrders'
import UploadPdf from './pages/UploadPdf'
import Orders from './pages/Orders'
import Clients from './pages/Clients'
import Update from './pages/Update'
import Zips from './pages/Zips'
import Settings from './pages/Settings'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/admin/login" element={<Login />} />
        <Route path="/admin" element={<App />}>
          <Route index element={<Dashboard />} />
          <Route path="files" element={<Files />} />
          <Route path="upload-orders" element={<UploadOrders />} />
          <Route path="upload-pdf" element={<UploadPdf />} />
          <Route path="orders" element={<Orders />} />
          <Route path="clients" element={<Clients />} />
          <Route path="update" element={<Update />} />
          <Route path="zips" element={<Zips />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
