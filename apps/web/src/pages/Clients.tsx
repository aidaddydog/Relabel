
import React, { useEffect, useState } from 'react'

export default function Clients(){
  const [desc, setDesc] = useState('')
  const [list, setList] = useState<any[]>([])

  async function load(){
    const res = await fetch('/api/v1/clients/by-code?code=123456')
    const data = await res.json()
    setDesc(data.description || '')
    setList(data.devices || [])
  }
  useEffect(()=>{ load() },[])
  return (
    <div>
      <h2>客户端</h2>
      <div>说明：{desc}</div>
      <table style={{width:'100%', marginTop:12}}>
        <thead><tr><th>主机</th><th>MAC</th><th>IP</th><th>最近</th><th>版本</th></tr></thead>
        <tbody>
          {list.map((d:any,idx:number)=>(
            <tr key={idx}>
              <td>{d.host}</td>
              <td>{(d.mac_list||[]).join(', ')}</td>
              <td>{(d.ip_list||[]).join(', ')}</td>
              <td>{d.last_seen}</td>
              <td>{d.client_version}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
