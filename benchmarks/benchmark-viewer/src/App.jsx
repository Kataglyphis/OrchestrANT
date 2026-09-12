import React, { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell,
} from 'recharts'

const DATA_URL = './benchmark_results/_manifest.json'

const COLORS = ['#6366f1', '#8b5cf6', '#a855f7', '#d946ef', '#ec4899']
const STATUS_COLORS = { success: '#22c55e', error: '#ef4444' }

function initFetch(url) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(setData)
      .catch(setErr)
  }, [url])
  return [data, err]
}

export function HardwareCard({ hw }) {
  if (!hw) return null
  const rows = [
    { label: 'OS', value: `${hw.os} ${hw.os_release}` },
    { label: 'Architecture', value: hw.architecture },
    { label: 'CPU', value: hw.cpu_model || 'unknown' },
    { label: 'Cores / Threads', value: `${hw.cpu_physical_cores ?? '?'} cores / ${hw.cpu_total_threads ?? '?'} threads` },
    { label: 'RAM', value: `${hw.ram_total_gb} GB` },
    { label: 'Container', value: hw.in_container ? 'Yes' : 'No' },
    { label: 'Ollama Host', value: hw.ollama_host },
  ]
  // A run whose host is unknown cannot be compared against another host later,
  // so an incomplete record is called out rather than quietly rendered blank.
  const missing = hw.incomplete || []
  return (
    <div className="card hardware-card">
      <h2>Hardware</h2>
      <table className="kv-table">
        <tbody>
          {rows.map(r => (
            <tr key={r.label}><td className="kv-label">{r.label}</td><td className="kv-value">{r.value}</td></tr>
          ))}
        </tbody>
      </table>
      {missing.length > 0 && (
        <p className="warn-note">
          Incomplete host record — missing: {missing.join(', ')}. Cross-host
          comparisons with this run are unreliable.
        </p>
      )}
    </div>
  )
}

// The headline question: is the model WORKING, not just fast? A broken model
// produces fluent nonsense at excellent tokens/sec, so this must outrank every
// speed number on the page.
export function CorrectnessBanner({ configs }) {
  const scored = configs.filter(c => c.correctness)
  if (scored.length === 0) {
    return (
      <div className="card full-width correctness-banner unknown">
        <h2>Correctness: not checked</h2>
        <p>
          These runs measured speed only. A model emitting garbage scores
          excellent tokens/sec, so speed alone cannot tell a working model from
          a broken one. Re-run with <code>--correctness</code> to verify.
        </p>
      </div>
    )
  }
  const total = scored.reduce((s, c) => s + c.correctness.total, 0)
  const score = scored.reduce((s, c) => s + c.correctness.score, 0)
  const ratio = total ? score / total : 0
  const state = ratio === 1 ? 'ok' : ratio >= 0.5 ? 'degraded' : 'broken'
  const headline = { ok: 'Correct', degraded: 'Degraded', broken: 'BROKEN' }[state]
  return (
    <div className={`card full-width correctness-banner ${state}`}>
      <h2>Correctness: {headline} — {score}/{total} verifiable answers</h2>
      {state !== 'ok' && (
        <p>
          Wrong answers here usually mean broken kernels or an over-aggressive
          quantisation, not a slow model. Check the GGUF tensor types
          (<code>inspect_gguf.py</code>) before tuning for speed — the speed
          numbers below are meaningless if the output is wrong.
        </p>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>Config</th><th>Score</th><th>Expected</th><th>Answer</th></tr>
          </thead>
          <tbody>
            {scored.map(c => c.correctness.items.map((it, i) => (
              <tr key={`${c.label}-${i}`} className={it.correct ? '' : 'row-bad'}>
                <td>{i === 0 ? <code>{c.label}</code> : ''}</td>
                <td>{it.correct ? 'ok' : 'FAIL'}</td>
                <td><code>{it.expected}</code></td>
                <td style={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={it.answer_preview || it.error}>
                  {it.answer_preview || it.error}
                </td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ModelCard({ title, model, generated, configs }) {
  const totalReqs = configs.reduce((s, c) => s + c.results.filter(r => !r.error).length, 0)
  const totalErrs = configs.reduce((s, c) => s + c.results.filter(r => r.error).length, 0)
  const avgTps = configs.length
    ? (configs.reduce((s, c) => {
        const ok = c.results.filter(r => r.tokens_per_sec)
        return s + ok.reduce((ss, r) => ss + r.tokens_per_sec, 0) / (ok.length || 1)
      }, 0) / configs.length).toFixed(1)
    : '-'
  return (
    <div className="card summary-card">
      <h2>{title}</h2>
      <div className="summary-stats">
        <div className="stat"><span className="stat-value">{model}</span><span className="stat-label">Model</span></div>
        <div className="stat"><span className="stat-value">{configs.length}</span><span className="stat-label">Configs</span></div>
        <div className="stat"><span className="stat-value">{totalReqs}</span><span className="stat-label">Requests</span></div>
        <div className="stat"><span className="stat-value">{totalErrs}</span><span className="stat-label">Errors</span></div>
        <div className="stat"><span className="stat-value">{avgTps}</span><span className="stat-label">Avg T/s</span></div>
        <div className="stat"><span className="stat-value">{new Date(generated).toLocaleDateString()}</span><span className="stat-label">Date</span></div>
      </div>
    </div>
  )
}

// Scored benchmarks — coding and tool calling. They were invisible in this
// viewer for as long as it existed: it read only the throughput tool's
// envelope, so the two benchmarks that answer "does the model WORK" never
// reached the one place a person actually looks.
export function ScoredRuns({ configs }) {
  const scored = configs.filter(c => (c.scored || []).length > 0)
  if (scored.length === 0) return null

  // Wilson interval, mirroring bench_stats.py: a bare fraction invites a
  // conclusion the sample cannot support, and 25/27 vs 27/27 do not differ.
  const wilson = (k, n) => {
    if (!n) return [0, 1]
    const z = 1.96, p = k / n, d = 1 + z * z / n
    const c = (p + z * z / (2 * n)) / d
    const h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [Math.max(0, c - h), Math.min(1, c + h)]
  }

  const rows = scored.flatMap(c => (c.scored || []).map(r => {
    const n = r.effective_n || r.total || 0
    const k = r.total ? Math.round(r.passed * n / r.total) : 0
    const [lo, hi] = wilson(k, n)
    return { kind: c.kind, file: c.label, label: r.label || r.model,
             passed: r.passed, total: r.total, n, lo, hi,
             truncated: r.truncated, errored: r.errored,
             wall: r.total_wall_s, median: r.median_wall_s,
             deterministic: r.deterministic }
  }))
  rows.sort((a, b) => (b.passed / (b.total || 1)) - (a.passed / (a.total || 1)))

  return (
    <div className="card full-width">
      <h2>Scored runs — coding and tool calling</h2>
      <p className="warn-note">
        Scores carry their 95% interval. Two rows whose intervals overlap are
        <strong> not separable</strong> at this sample size, however different
        the fractions look.
      </p>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Benchmark</th><th>Model</th>
              <th title="Passed of attempted, with the 95% Wilson interval">Score [95% CI]</th>
              <th title="Cut off by a server output cap — unmeasured, not failed">cut</th>
              <th title="Transport failures, excluded from the denominator">err</th>
              <th>Total (s)</th>
              <th title="Repeats on a deterministic endpoint add no information">det.</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.file}-${r.label}-${i}`}
                  style={{ background: i % 2 === 0 ? 'var(--bg-alt)' : undefined }}>
                <td><code>{r.kind.replace('bench_', '')}</code></td>
                <td style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis',
                             whiteSpace: 'nowrap' }} title={r.label}>{r.label}</td>
                <td className="num strong">
                  {r.passed}/{r.total} = {r.total ? Math.round(100 * r.passed / r.total) : 0}%
                  {' '}[{Math.round(100 * r.lo)}–{Math.round(100 * r.hi)}%]
                </td>
                <td className="num">{r.truncated || 0}</td>
                <td className="num">{r.errored || 0}</td>
                <td className="num">{r.wall != null ? r.wall.toFixed(1) : '-'}</td>
                <td className="num">{r.deterministic ? 'yes' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ComparisonTable({ configs }) {
  const rows = configs.map(c => {
    const ok = c.results.filter(r => !r.error)
    const tps = ok.length ? (ok.reduce((s, r) => s + r.tokens_per_sec, 0) / ok.length).toFixed(1) : '-'
    const lat = ok.length ? (ok.reduce((s, r) => s + r.latency_s, 0) / ok.length).toFixed(1) : '-'
    // TTFT is only produced by streaming runs; show '-' rather than a fake 0.
    const withTtft = ok.filter(r => r.ttft_s != null)
    const ttft = withTtft.length ? (withTtft.reduce((s, r) => s + r.ttft_s, 0) / withTtft.length).toFixed(2) : '-'
    const withDec = ok.filter(r => r.decode_tok_per_sec)
    const dec = withDec.length ? (withDec.reduce((s, r) => s + r.decode_tok_per_sec, 0) / withDec.length).toFixed(1) : '-'
    const withThink = ok.filter(r => r.thinking_char_share != null)
    const think = withThink.length
      ? (100 * withThink.reduce((s, r) => s + r.thinking_char_share, 0) / withThink.length).toFixed(0) + '%'
      : '-'
    const answer = ok.length ? (ok.reduce((s, r) => s + (r.wall_s_to_answer ?? r.latency_s), 0) / ok.length).toFixed(1) : '-'
    const cpu = ok.length ? (ok.reduce((s, r) => s + r.cpu_percent, 0) / ok.length).toFixed(1) : '-'
    const ram = ok.length ? (ok.reduce((s, r) => s + r.ram_used_gb, 0) / ok.length).toFixed(1) : '-'
    const comp = ok.reduce((s, r) => s + (r.completion_tokens || 0), 0)
    const prom = ok.reduce((s, r) => s + (r.prompt_tokens || 0), 0)
    return { label: c.label, tps, lat, ttft, dec, think, answer, cpu, ram, comp, prom, numOk: ok.length }
  })
  return (
    <div className="card full-width">
      <h2>Config Comparison</h2>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Config</th>
              <th>num_ctx</th>
              <th>max_tokens</th>
              <th title="Wall time to a finished answer — rank by this, not by T/s">Answer (s)</th>
              <th title="Time to first token: what a user actually waits on before anything appears">TTFT (s)</th>
              <th title="Decode rate excluding prefill">Decode T/s</th>
              <th title="Overall rate; divides by the whole request, so it mixes prefill in">T/s</th>
              <th title="Share of the output spent inside a &lt;think&gt; block — pure latency for an agent">Think</th>
              <th>CPU %</th>
              <th>RAM (GB)</th>
              <th>Comp. Tokens</th>
              <th>Prompt Tokens</th>
              <th>OK</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const ctx = r.label.match(/ctx(\d+)/)?.[1] ?? '?'
              const tok = r.label.match(/tok(\d+)/)?.[1] ?? '?'
              return (
                <tr key={r.label} style={{ background: i % 2 === 0 ? 'var(--bg-alt)' : undefined }}>
                  <td><code>{r.label}</code></td>
                  <td>{ctx}</td>
                  <td>{tok}</td>
                  <td className="num strong">{r.answer}</td>
                  <td className="num">{r.ttft}</td>
                  <td className="num">{r.dec}</td>
                  <td className="num">{r.tps}</td>
                  <td className="num">{r.think}</td>
                  <td className="num">{r.cpu}</td>
                  <td className="num">{r.ram}</td>
                  <td className="num">{r.comp}</td>
                  <td className="num">{r.prom}</td>
                  <td className="num">{r.numOk}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ChartSection({ configs, title, dataKey, label, unit, decimals }) {
  // Average ONLY over results that actually carry this metric, and drop a
  // config entirely if none do. Older result files predate ttft_s/decode rate;
  // treating a missing value as 0 would draw a bar claiming an instant first
  // token, which is worse than drawing nothing.
  const data = configs.map((c, i) => {
    const vals = c.results.filter(r => !r.error && typeof r[dataKey] === 'number')
    if (vals.length === 0) return null
    const avg = vals.reduce((s, r) => s + r[dataKey], 0) / vals.length
    return { name: c.label, value: parseFloat(avg.toFixed(decimals ?? 1)), fill: COLORS[i % COLORS.length] }
  }).filter(Boolean)

  if (data.length === 0) {
    return (
      <div className="card chart-card">
        <h2>{title}</h2>
        <p className="warn-note">
          No run recorded <code>{dataKey}</code>. Streaming metrics need
          <code> --stream</code>; older result files predate them.
        </p>
      </div>
    )
  }

  const partial = data.length < configs.length
  return (
    <div className="card chart-card">
      <h2>{title}</h2>
      {partial && (
        <p className="warn-note">
          Showing {data.length} of {configs.length} runs — the rest did not
          record <code>{dataKey}</code>.
        </p>
      )}
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 60 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="name" angle={-25} textAnchor="end" tick={{ fontSize: 11 }} interval={0} />
          <YAxis label={{ value: unit, angle: -90, position: 'insideLeft', style: { fontSize: 12 } }} />
          <Tooltip formatter={v => [`${v} ${unit}`, label]} />
          <Bar dataKey="value" name={label} radius={[4, 4, 0, 0]}>
            {data.map((e, i) => <Cell key={i} fill={e.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function ConfigDetail({ config, defaultExpanded = false }) {
  // defaultExpanded exists so the smoke render can exercise the per-prompt
  // table -- the most guard-heavy markup here, and the place a missing-field
  // crash would otherwise hide behind a collapsed panel.
  const [expanded, setExpanded] = useState(defaultExpanded)
  const ok = config.results.filter(r => !r.error)
  const errs = config.results.filter(r => r.error)
  return (
    <div className="card config-detail">
      <div className="config-detail-header" onClick={() => setExpanded(!expanded)}>
        <h3><code>{config.label}</code></h3>
        <span className="toggle">{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div className="config-detail-body">
          <table className="kv-table">
            <tbody>
              {Object.entries(config.config?.extra_params || {}).map(([k, v]) => (
                <tr key={k}><td className="kv-label">{k}</td><td className="kv-value">{JSON.stringify(v)}</td></tr>
              ))}
              {Object.entries(config.config || {}).filter(([k]) => !['extra_params', 'prompts_requested', 'prompts_completed'].includes(k)).map(([k, v]) => (
                <tr key={k}><td className="kv-label">{k}</td><td className="kv-value">{JSON.stringify(v)}</td></tr>
              ))}
            </tbody>
          </table>
          {ok.length > 0 && (
            <>
              <h4>Per-Prompt Results</h4>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Prompt</th>
                      <th>PT</th>
                      <th>CT</th>
                      <th title="Wall time to a finished answer">Answer (s)</th>
                      <th title="Time to first token">TTFT (s)</th>
                      <th title="Decode rate excluding prefill">Decode</th>
                      <th title="Prompt tokens processed per second before the first token">Prefill</th>
                      <th title="Share of output inside a &lt;think&gt; block">Think</th>
                      <th>T/s</th>
                      <th>CPU%</th>
                      <th>RAM (GB)</th>
                      <th title="Process that burned the most CPU during this request — not necessarily the one owning the port">Busiest proc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ok.map(r => (
                      <tr key={r.prompt_index} style={{ background: r.prompt_index % 2 === 0 ? 'var(--bg-alt)' : undefined }}>
                        <td>{r.prompt_index}</td>
                        <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.prompt_preview}>{r.prompt_preview}</td>
                        <td className="num">{r.prompt_tokens}</td>
                        <td className="num" title={r.tokens_estimated ? 'Estimated from streamed chunks: this server did not report usage' : undefined}>
                          {r.completion_tokens}{r.tokens_estimated ? '*' : ''}
                        </td>
                        <td className="num strong">{(r.wall_s_to_answer ?? r.latency_s)?.toFixed(1)}</td>
                        <td className="num">{r.ttft_s != null ? r.ttft_s.toFixed(2) : '-'}</td>
                        <td className="num">{r.decode_tok_per_sec != null ? r.decode_tok_per_sec.toFixed(1) : '-'}</td>
                        <td className="num">{r.prefill_tok_per_sec != null ? r.prefill_tok_per_sec.toFixed(0) : '-'}</td>
                        <td className="num">{r.thinking_char_share != null ? `${(100 * r.thinking_char_share).toFixed(0)}%` : '-'}</td>
                        <td className="num">{r.tokens_per_sec?.toFixed(1)}</td>
                        <td className="num">{r.cpu_percent?.toFixed(1)}</td>
                        <td className="num">{r.ram_used_gb?.toFixed(2)}</td>
                        <td title={(r.top_processes || []).map(p => `${p.name} (pid ${p.pid}) ${p.cpu_percent}%`).join(', ')}>
                          {r.top_processes?.[0]
                            ? `${r.top_processes[0].name} ${r.top_processes[0].cpu_percent}%`
                            : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {ok.some(r => r.tokens_estimated) && (
                <p className="warn-note">
                  * token counts estimated from streamed chunks — this server
                  does not report <code>usage</code>, so rates are approximate.
                </p>
              )}
            </>
          )}
          {errs.length > 0 && (
            <>
              <h4>Errors ({errs.length})</h4>
              <ul className="error-list">
                {errs.map((r, i) => (
                  <li key={i}><strong>#{r.prompt_index}</strong>: {r.error}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function useTheme() {
  const getInitial = () => {
    const stored = localStorage.getItem('kataglyphis-theme')
    if (stored) return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  const [theme, setTheme] = useState(getInitial)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('kataglyphis-theme', theme)
  }, [theme])
  return [theme, () => setTheme(t => t === 'light' ? 'dark' : 'light')]
}

function App() {
  const [data, err] = initFetch(DATA_URL)
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [theme, toggleTheme] = useTheme()

  if (err) return (
    <div className="app">
      <div className="error-banner">
        <strong>Failed to load benchmark data.</strong>
        <p>{err.message}</p>
        <p>Make sure <code>benchmark_results/_manifest.json</code> exists relative to the app.</p>
        <p>Run <code>bash run_benchmarks.sh</code> first, then rebuild the viewer.</p>
      </div>
    </div>
  )
  if (!data) return (
    <div className="app">
      <div className="loading">Loading benchmark data…</div>
    </div>
  )

  const { title, model, generated, host_hardware: hw, configs } = data
  const selected = selectedIdx !== null ? configs[selectedIdx] : null

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>LLM Benchmark Viewer</h1>
          <p className="subtitle">{title || 'Model benchmarks'}</p>
        </div>
        <button className="theme-toggle" onClick={toggleTheme}>
          {theme === 'light' ? '🌙 Dark' : '☀️ Light'}
        </button>
      </header>

      <div className="layout-top">
        <ModelCard title={title} model={model} generated={generated} configs={configs} />
        <HardwareCard hw={hw} />
      </div>

      <CorrectnessBanner configs={configs} />

      <ScoredRuns configs={configs} />

      <ComparisonTable configs={configs} />

      <div className="chart-grid">
        <ChartSection configs={configs} title="Time to Finished Answer" dataKey="wall_s_to_answer" label="Answer" unit="s" />
        <ChartSection configs={configs} title="Time to First Token" dataKey="ttft_s" label="TTFT" unit="s" decimals={2} />
        <ChartSection configs={configs} title="Decode Rate (excl. prefill)" dataKey="decode_tok_per_sec" label="Decode" unit="tok/s" />
        <ChartSection configs={configs} title="Tokens per Second (overall)" dataKey="tokens_per_sec" label="T/s" unit="tok/s" />
        <ChartSection configs={configs} title="CPU Usage" dataKey="cpu_percent" label="CPU" unit="%" />
        <ChartSection configs={configs} title="RAM Usage" dataKey="ram_used_gb" label="RAM" unit="GB" decimals={2} />
      </div>

      <div className="card full-width">
        <h2>Drill Down</h2>
        <div className="drill-selector">
          {configs.map((c, i) => (
            <button
              key={c.label}
              className={`chip ${selectedIdx === i ? 'active' : ''}`}
              onClick={() => setSelectedIdx(selectedIdx === i ? null : i)}
            >
              {c.label}
            </button>
          ))}
        </div>
        {selected && <ConfigDetail config={selected} />}
      </div>

      <footer className="app-footer">
        Generated {new Date(generated).toLocaleString()} &middot; LLM Stack Benchmark Suite
      </footer>
    </div>
  )
}

export default App
