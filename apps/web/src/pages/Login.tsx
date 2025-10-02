
import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Login() {
  const nav = useNavigate()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123')
  const [err, setErr] = useState('')
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    const res = await fetch('/admin/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username, password})
    })
    if (res.ok) nav('/admin')
    else setErr('登录失败')
  }
  return (
    <div style={{display:'grid', placeItems:'center', height:'100vh'}}>
      <form onSubmit={submit} style={{display:'grid', gap:12, width:320}}>
        <h2>Relabel 登录</h2>
        <input value={username} onChange={e=>setUsername(e.target.value)} placeholder="用户名" />
        <input value={password} onChange={e=>setPassword(e.target.value)} placeholder="密码" type="password" />
        <button type="submit">登录</button>
        {err && <div style={{color:'red'}}>{err}</div>}
      </form>
    </div>
  )
}
