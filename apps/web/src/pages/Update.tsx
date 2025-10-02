
import React, { useEffect, useState } from 'react'

export default function Update(){
  const [info,setInfo] = useState<any>({})
  const [out,setOut] = useState('')
  useEffect(()=>{ (async()=>{
    const res = await fetch('/admin/update')
    const data = await res.json()
    setInfo(data)
  })() },[])
  async function pull(){
    const res = await fetch('/admin/update/git_pull', {method:'POST'})
    const data = await res.json()
    setOut(JSON.stringify(data, null, 2))
  }
  return (
    <div>
      <h2>在线更新</h2>
      <pre>{JSON.stringify(info,null,2)}</pre>
      <button onClick={pull}>一键更新</button>
      <pre>{out}</pre>
    </div>
  )
}
