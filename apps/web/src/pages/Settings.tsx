
import React, { useEffect, useState } from 'react'

export default function Settings(){
  const [items,setItems] = useState<any>({})
  useEffect(()=>{ (async()=>{
    const res = await fetch('/admin/api/settings')
    const data = await res.json()
    setItems(data.items||{})
  })() },[])

  async function save(){
    await fetch('/admin/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(items)})
    alert('已保存')
  }

  return (
    <div>
      <h2>系统设置</h2>
      <div style={{display:'grid', gap:8, maxWidth:320}}>
        <label>订单保留天 <input value={items.o_days||''} onChange={e=>setItems({...items, o_days: e.target.value})} /></label>
        <label>PDF保留天 <input value={items.f_days||''} onChange={e=>setItems({...items, f_days: e.target.value})} /></label>
        <label>版本号 <input value={items.version||''} onChange={e=>setItems({...items, version: e.target.value})} /></label>
        <label>推荐客户端 <input value={items.client_recommend||''} onChange={e=>setItems({...items, client_recommend: e.target.value})} /></label>
      </div>
      <button onClick={save} style={{marginTop:8}}>保存</button>
    </div>
  )
}
