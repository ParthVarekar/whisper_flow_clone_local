import Link from 'next/link'
import { Button } from '@/components/ui/button'

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="max-w-md w-full text-center space-y-4">
        <h2 className="text-4xl font-bold text-muted-foreground">404</h2>
        <p className="text-sm text-muted-foreground">This page could not be found.</p>
        <Link href="/">
          <Button variant="default">Go home</Button>
        </Link>
      </div>
    </div>
  )
}
