import { useEffect } from 'react'
import { useRouter } from 'next/router'
import { isAuthed } from '../lib/auth'

export default function Index() {
  const router = useRouter()
  useEffect(() => {
    void router.replace(isAuthed() ? '/dashboard' : '/login')
  }, [router])
  return null
}