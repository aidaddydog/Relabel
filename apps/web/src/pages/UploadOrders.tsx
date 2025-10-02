
import React, { useState } from 'react'

export default function UploadOrders(){
  const [tmp, setTmp] = useState<any>(null)
  const [orderCol, setOrderCol] = useState('')
  const [trackCol, setTrackCol] = useState('')
  const [logs, setLogs] = useState<string[]>([])

  async function step1(e: React.FormEvent){
    e.preventDefault()
    const f = (document.getElementById('file') as HTMLInputElement).files?.[0]
    if (!f) return alert('请选择文件')
    const fd = new FormData()
    fd.append('file', f)
    const res = await fetch('/admin/api/upload-orders-step1', {method:'POST', body: fd})
    const data = await res.json()
    setTmp(data)
  }
  async function step2(e: React.FormEvent){
    e.preventDefault()
    const res = await fetch('/admin/api/upload-orders-step2', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({tmp_path: tmp.tmp_path, order_col: orderCol, tracking_col: trackCol})})
    const data = await res.json()
    setTmp(data)
  }
  async function apply(){
    const url = new URL('/admin/api/orders-apply', window.location.origin)
    url.searchParams.set('tmp_path', tmp.tmp_path)
    url.searchParams.set('order_col', tmp.order_col || orderCol)
    url.searchParams.set('tracking_col', tmp.tracking_col || trackCol)
    const es = new EventSource(url.toString())
    es.onmessage = (ev)=>{
      const msg = JSON.parse(ev.data)
      setLogs(prev=>[...prev, JSON.stringify(msg)])
      if (msg.phase === 'done' || msg.phase === 'error') es.close()
    }
  }

  return (
    <div>
      <h2>导入订单</h2>
      {!tmp && <form onSubmit={step1} style={{display:'grid', gap:8}}>
        <input id="file" type="file" accept=".xls,.xlsx,.csv" />
        <button type="submit">下一步</button>
      </form>}
      {!!tmp && !tmp.order_col && <form onSubmit={step2} style={{display:'grid', gap:8}}>
        <div>请选择列（来自表头）：</div>
        <select value={orderCol} onChange={e=>setOrderCol(e.target.value)}>
          <option value="">订单号列</option>
          {tmp.columns?.map((c:string)=>(<option key={c} value={c}>{c}</option>))}
        </select>
        <select value={trackCol} onChange={e=>setTrackCol(e.target.value)}>
          <option value="">运单号列</option>
          {tmp.columns?.map((c:string)=>(<option key={c} value={c}>{c}</option>))}
        </select>
        <button type="submit">下一步</button>
      </form>}
      {!!tmp && (tmp.order_col || orderCol) && <div>
        <button onClick={apply}>确认导入（SSE）</button>
      </div>}
      <pre style={{marginTop:12, background:'#f8fafc', padding:8, height:200, overflow:'auto'}}>{logs.join('\n')}</pre>
    </div>
  )
}
