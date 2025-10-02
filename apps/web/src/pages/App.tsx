
import React from 'react'
import { Outlet, Link, useNavigate } from 'react-router-dom'

export default function App() {
  const nav = useNavigate()
  async function logout() {
    await fetch('/admin/logout')
    nav('/admin/login')
  }
  return (
    <div style={{display:'flex', minHeight:'100vh'}}>
      <aside style={{width:240, background:'#0f172a', color:'white', padding:16}}>
        <h2>Relabel</h2>
        <nav style={{display:'flex', flexDirection:'column', gap:8, marginTop:16}}>
          <Link to="/admin">仪表盘</Link>
          <Link to="/admin/upload-orders">导入订单</Link>
          <Link to="/admin/upload-pdf">导入PDF</Link>
          <Link to="/admin/orders">订单列表</Link>
          <Link to="/admin/files">PDF列表</Link>
          <Link to="/admin/clients">客户端</Link>
          <Link to="/admin/update">在线升级</Link>
          <Link to="/admin/zips">ZIP 包</Link>
          <Link to="/admin/templates">模板编辑（请用 API）</Link>
          <Link to="/admin/settings">系统设置</Link>
          <button onClick={logout} style={{marginTop:16}}>退出</button>
        </nav>
      </aside>
      <main style={{flex:1, padding:24}}>
        <Outlet />
      </main>
    </div>
  )
}
