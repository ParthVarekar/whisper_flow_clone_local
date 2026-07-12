import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/export — download all notes as a single markdown file (B2)
//
// Returns a text/markdown response containing every note (except archived
// ones, unless ?include_archived=true), organized by type, with YAML
// frontmatter for each note. This is the portability escape hatch — the
// brain never holds your data hostage. Open the result in Obsidian, VS Code,
// or any text editor.
//
// We return a single .md file (not a zip) for maximum compatibility — no
// archiver needed, opens in any editor. Each note is an H2 section under
// its type, separated by horizontal rules.
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const includeArchived = searchParams.get('include_archived') === 'true'

  const where: any = includeArchived ? {} : { status: { not: 'archived' } }
  const notes = await db.note.findMany({
    where,
    orderBy: [{ type: 'asc' }, { createdAt: 'desc' }],
    include: { tasks: true },
  })

  const typeOrder = ['idea', 'task', 'reference', 'question', 'journal', 'dictation', 'uncategorized']
  const byType = new Map<string, typeof notes>()
  for (const n of notes) {
    const arr = byType.get(n.type) || []
    arr.push(n)
    byType.set(n.type, arr)
  }

  const lines: string[] = []
  lines.push('# Second Brain Export')
  lines.push('')
  lines.push(`> Exported ${new Date().toISOString()}`)
  lines.push(`> ${notes.length} notes${includeArchived ? ' (including archived)' : ''}`)
  lines.push('')
  lines.push('---')
  lines.push('')

  for (const type of typeOrder) {
    const arr = byType.get(type)
    if (!arr || arr.length === 0) continue

    lines.push(`# ${type.charAt(0).toUpperCase() + type.slice(1)}s (${arr.length})`)
    lines.push('')

    for (const n of arr) {
      // YAML frontmatter
      lines.push('---')
      lines.push(`id: ${n.id}`)
      lines.push(`type: ${n.type}`)
      lines.push(`tags: [${n.tags.split(',').filter(Boolean).map(t => `"${t}"`).join(', ')}]`)
      lines.push(`source: ${n.source}`)
      lines.push(`status: ${n.status}`)
      lines.push(`confidence: ${n.confidence}`)
      lines.push(`created: ${n.createdAt.toISOString()}`)
      lines.push(`updated: ${n.updatedAt.toISOString()}`)
      if (n.appContext) lines.push(`app_context: "${n.appContext.replace(/"/g, '\\"')}"`)
      lines.push('---')
      lines.push('')
      lines.push(`## ${n.title}`)
      lines.push('')
      lines.push(n.body)
      lines.push('')
      if (n.tasks.length > 0) {
        lines.push('### Tasks')
        lines.push('')
        for (const t of n.tasks) {
          const box = t.done ? '[x]' : '[ ]'
          const due = t.due ? ` ⏰ ${new Date(t.due).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}` : ''
          lines.push(`- ${box} ${t.text}${due}`)
        }
        lines.push('')
      }
      lines.push('---')
      lines.push('')
    }
  }

  // Backlinks index (useful for Obsidian-style navigation)
  const links = await db.link.findMany({
    include: { source: { select: { title: true } }, target: { select: { title: true } } },
  })
  if (links.length > 0) {
    lines.push('# Link Index')
    lines.push('')
    for (const l of links) {
      const target = l.target ? l.target.title : '(deleted)'
      lines.push(`- [[${l.source.title}]] → [[${target}]] (${l.type}, ${(l.similarity * 100).toFixed(0)}%)`)
    }
    lines.push('')
  }

  const md = lines.join('\n')
  const filename = `second-brain-${new Date().toISOString().slice(0, 10)}.md`

  return new Response(md, {
    headers: {
      'Content-Type': 'text/markdown; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Length': String(Buffer.byteLength(md, 'utf-8')),
    },
  })
}
