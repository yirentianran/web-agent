import { useState, useEffect } from 'react'
import type { EvolutionDiff, EvolutionApi } from '../../hooks/useEvolutionApi'
import { DiffEditor } from '@monaco-editor/react'

interface Props {
  evolutionId: number
  api: EvolutionApi
}

export default function VersionDiff({ evolutionId, api }: Props) {
  const [data, setData] = useState<EvolutionDiff | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api
      .fetchDiff(evolutionId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [evolutionId, api])

  if (loading) return <div className="evo-loading">Loading diff...</div>
  if (!data || !data.diff) {
    return <div className="evo-empty">Diff not available</div>
  }

  const lines = data.diff.split('\n')
  const originalLines: string[] = []
  const modifiedLines: string[] = []

  for (const line of lines) {
    if (line.startsWith('-') && !line.startsWith('---')) {
      originalLines.push(line.slice(1))
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      modifiedLines.push(line.slice(1))
    } else if (line.startsWith(' ') || line === '') {
      originalLines.push(line.startsWith(' ') ? line.slice(1) : line)
      modifiedLines.push(line.startsWith(' ') ? line.slice(1) : line)
    }
  }

  return (
    <div className="evo-diff">
      <h4>Version Diff</h4>
      <DiffEditor
        original={originalLines.join('\n')}
        modified={modifiedLines.join('\n')}
        language="markdown"
        options={{ readOnly: true, renderSideBySide: true }}
        height="400px"
        theme="vs-dark"
      />
    </div>
  )
}
