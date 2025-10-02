
import React, { useEffect, useState } from 'react'

export default function Dashboard() {
  const [stats,setStats] = useState<any>({})
  useEffect(()=>{
    (async()=>{
      const files = await (await fetch('/admin/api/files')).json()
      const orders = await (await fetch('/admin/api/orders')).json()
      setStats({
        files_total: files.total,
        orders_total: orders.total
      })
    })()
  },[])
  return (
    <div>
      <h2>仪表盘</h2>
      <div style={{display:'flex', gap:16}}>
        <div style={{padding:16, border:'1px solid #eee'}}>PDF总数：{stats.files_total||0}</div>
        <div style={{padding:16, border:'1px solid #eee'}}>订单总数：{stats.orders_total||0}</div>
      </div>
    </div>
  )
}
