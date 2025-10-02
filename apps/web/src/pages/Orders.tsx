
import React, { useEffect, useState } from 'react'

export default function Orders(){
  const [items,setItems] = useState<any[]>([])
  const [q,setQ] = useState('')
  async function load(){
    const url = new URL('/admin/api/orders', window.location.origin)
    if (q) url.searchParams.set('q', q)
    const res = await fetch(url.toString())
    const data = await res.json()
    setItems(data.items||[])
  }
  useEffect(()=>{ load() },[])
  return (
    <div>
      <h2>订单列表</h2>
      <div style={{display:'flex', gap:8}}>
        <input value={q} onChange={e=>setQ(e.target.value)} placeholder="按订单号搜索" />
        <button onClick={load}>查询</button>
        <a href="/admin/api/orders/export-xlsx" target="_blank" rel="noreferrer">导出Excel</a>
      </div>
      <table style={{width:'100%', marginTop:12}}>
        <thead><tr><th>订单号</th><th>运单号</th><th>更新时间</th></tr></thead>
        <tbody>
          {items.map(it=>(
            <tr key={it.order_id}>
              <td>{it.order_id}</td>
              <td>{it.tracking_no}</td>
              <td>{it.updated_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
