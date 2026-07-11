/**
 * Markdown vault storage — the portability layer.
 *
 * Every note is synced to a .md file in a vault directory (default:
 * ~/SecondBrain/). The .md files are Obsidian-compatible with YAML
 * frontmatter. This means:
 *
 *   1. No lock-in: the user can open the vault in Obsidian, VS Code, or any
 *      text editor. The brain never holds data hostage.
 *   2. Future-proof: if the app breaks, the .md files are still readable.
 *   3. Sync-ready: the vault is just a folder — Syncthing/Dropbox/iCloud
 *      can sync it to other devices.
 *
 * The SQLite DB remains the source of truth for the app's query/index needs
 * (FTS, tags, links, tasks). The vault is a derived projection that can be
 * rebuilt from the DB at any time (see `rebuildVault()`).
 *
 * File layout:
 *   ~/SecondBrain/
 *     Inbox/        — status='inbox' notes (regardless of type)
 *     Ideas/        — type='idea'
 *     Tasks/        — type='task'
 *     References/   — type='reference'
 *     Questions/    — type='question'
 *     Daily/        — type='journal'
 *     Dictation/    — type='dictation'
 *     _index.md     — auto-generated tag + link index
 */
import { promises as fs } from 'fs'
import path from 'path'
import os from 'os'

const VAULT_DIR = process.env.SECOND_BRAIN_VAULT || path.join(os.homedir(), 'SecondBrain')

const TYPE_TO_FOLDER: Record<string, string> = {
  idea: 'Ideas',
  task: 'Tasks',
  reference: 'References',
  question: 'Questions',
  journal: 'Daily',
  dictation: 'Dictation',
  uncategorized: 'Inbox',
}

function folderFor(note: { type: string; status: string }): string {
  // Inbox notes go to Inbox/ regardless of type, so they surface for review
  if (note.status === 'inbox') return 'Inbox'
  if (note.status === 'archived') return 'Archive'
  return TYPE_TO_FOLDER[note.type] || 'Daily'
}

/** Sanitize a title into a filesystem-safe filename. */
function safeFilename(title: string): string {
  return title
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80) || 'untitled'
}

/** Full vault-relative path for a note's .md file. */
export function vaultPathFor(note: { id: string; title: string; type: string; status: string }): string {
  const folder = folderFor(note)
  const filename = `${safeFilename(note.title)}-${note.id.slice(-6)}.md`
  return path.join(folder, filename)
}

/** Full absolute path on disk. */
function absVaultPath(vaultRelPath: string): string {
  return path.join(VAULT_DIR, vaultRelPath)
}

/** Ensure the vault + type folders exist. */
async function ensureVaultDirs(): Promise<void> {
  const dirs = ['Inbox', 'Ideas', 'Tasks', 'References', 'Questions', 'Daily', 'Dictation', 'Archive']
  await fs.mkdir(VAULT_DIR, { recursive: true })
  await Promise.all(dirs.map(d => fs.mkdir(path.join(VAULT_DIR, d), { recursive: true })))
}

/** Build the .md file content with YAML frontmatter. */
function renderMarkdown(note: {
  id: string
  title: string
  body: string
  type: string
  tags: string
  source: string
  status: string
  confidence: number
  appContext: string | null
  createdAt: Date
  updatedAt: Date
  tasks?: { text: string; done: boolean; due: Date | null }[]
  links?: { target: { title: string } | null; similarity: number; type: string }[]
}): string {
  const tags = note.tags.split(',').filter(Boolean).map(t => `"${t.trim()}"`).join(', ')
  const lines: string[] = []

  // YAML frontmatter
  lines.push('---')
  lines.push(`id: ${note.id}`)
  lines.push(`type: ${note.type}`)
  lines.push(`tags: [${tags}]`)
  lines.push(`source: ${note.source}`)
  lines.push(`status: ${note.status}`)
  lines.push(`confidence: ${note.confidence}`)
  lines.push(`created: ${note.createdAt.toISOString()}`)
  lines.push(`updated: ${note.updatedAt.toISOString()}`)
  if (note.appContext) lines.push(`app_context: "${note.appContext.replace(/"/g, '\\"')}"`)
  lines.push('---')
  lines.push('')
  lines.push(`# ${note.title}`)
  lines.push('')
  lines.push(note.body)
  lines.push('')

  // Tasks
  if (note.tasks && note.tasks.length > 0) {
    lines.push('## Tasks')
    lines.push('')
    for (const t of note.tasks) {
      const box = t.done ? '[x]' : '[ ]'
      const due = t.due ? ` ⏰ ${new Date(t.due).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}` : ''
      lines.push(`- ${box} ${t.text}${due}`)
    }
    lines.push('')
  }

  // Related links (wikilinks for Obsidian)
  if (note.links && note.links.length > 0) {
    lines.push('## Related')
    lines.push('')
    for (const l of note.links) {
      if (l.target) {
        lines.push(`- [[${l.target.title}]] (${l.type}, ${(l.similarity * 100).toFixed(0)}%)`)
      }
    }
    lines.push('')
  }

  return lines.join('\n')
}

/**
 * Write (or overwrite) a note's .md file in the vault.
 * Returns the vault-relative path, or null if the vault is unavailable.
 */
export async function writeNoteToVault(note: {
  id: string
  title: string
  body: string
  type: string
  tags: string
  source: string
  status: string
  confidence: number
  appContext: string | null
  createdAt: Date
  updatedAt: Date
  tasks?: { text: string; done: boolean; due: Date | null }[]
  links?: { target: { title: string } | null; similarity: number; type: string }[]
}): Promise<string | null> {
  try {
    await ensureVaultDirs()
    const relPath = vaultPathFor(note)
    const absPath = absVaultPath(relPath)
    const content = renderMarkdown(note)
    await fs.writeFile(absPath, content, 'utf-8')
    return relPath
  } catch (err) {
    // Vault is best-effort — never block a capture because the vault write failed
    console.error('[vault] write failed:', err instanceof Error ? err.message : err)
    return null
  }
}

/**
 * Remove a note's .md file from the vault.
 * @param vaultRelPath The vault-relative path stored in Note.vaultPath
 */
export async function removeNoteFromVault(vaultRelPath: string | null): Promise<void> {
  if (!vaultRelPath) return
  try {
    const absPath = absVaultPath(vaultRelPath)
    await fs.unlink(absPath)
  } catch (err: any) {
    if (err?.code !== 'ENOENT') {
      console.error('[vault] remove failed:', err?.message || err)
    }
  }
}

/**
 * Move a note's .md file when its type/status changes (folder may change).
 * Deletes the old file and writes the new one.
 */
export async function moveNoteInVault(
  oldVaultRelPath: string | null,
  note: Parameters<typeof writeNoteToVault>[0],
): Promise<string | null> {
  await removeNoteFromVault(oldVaultRelPath)
  return writeNoteToVault(note)
}

/** Get the vault directory path (for the UI / settings). */
export function getVaultDir(): string {
  return VAULT_DIR
}
