
import React, { useEffect, useState } from 'react'

export default function Zips(){
  const [items,setItems] = useState<any[]>([])
  useEffect(()=>{ (async()=>{
    const res = await fetch('/admin/api/zips')
    const data = await res.json()
    setItems(data.items||[])
  })() },[])
  return (
    <div>
      <h2>ZIP 包列表</h2>
      <table style={{width:'100%'}}>
        <thead><tr><th>文件</th><th>大小</th></tr></thead>
        <tbody>
          {items.map((it:any)=>(
            <tr key={it.file}>
              <td>{it.file}</td>
              <td>{it.size}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
