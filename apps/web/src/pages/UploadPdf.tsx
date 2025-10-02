
import React, { useState } from 'react'

export default function UploadPdf(){
  const [token, setToken] = useState<string>('')
  const [logs, setLogs] = useState<string[]>([])

  async function upload(e: React.FormEvent){
    e.preventDefault()
    const f = (document.getElementById('zip') as HTMLInputElement).files?.[0]
    if (!f) return alert('请选择ZIP')
    const fd = new FormData()
    fd.append('file', f)
    const res = await fetch('/admin/api/upload-pdf-file', {method:'POST', body: fd})
    const data = await res.json()
    setToken(data.token)
  }

  function apply(){
    const url = new URL('/admin/api/apply-pdf-import', window.location.origin)
    url.searchParams.set('token', token)
    const es = new EventSource(url.toString())
    es.onmessage = ev => {
      const msg = JSON.parse(ev.data)
      setLogs(prev => [...prev, JSON.stringify(msg)])
      if (msg.phase === 'done' || msg.phase === 'error') es.close()
    }
  }

  return (
    <div>
      <h2>导入PDF（ZIP）</h2>
      <form onSubmit={upload} style={{display:'flex', gap:8}}>
        <input id="zip" type="file" accept=".zip" />
        <button type="submit">上传</button>
      </form>
      {token && <button onClick={apply} style={{marginTop:8}}>开始应用（SSE）</button>}
      <pre style={{marginTop:12, background:'#f8fafc', padding:8, height:200, overflow:'auto'}}>{logs.join('\n')}</pre>
    </div>
  )
}
